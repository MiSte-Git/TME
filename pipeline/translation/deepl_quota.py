"""
Persistentes Monats-Kontingent-Tracking für das DeepL-Free-Tier
(500.000 Zeichen pro Abrechnungszeitraum, siehe DEEPL_FREE_CHARACTER_LIMIT).

DeepL bietet über GET /v2/usage den tatsächlichen, konto-weiten Verbrauch
der laufenden Abrechnungsperiode an - authoritativ, lebt bei DeepL selbst
(siehe DeepLProvider.get_usage() in deepl_provider.py):

    GET https://api-free.deepl.com/v2/usage  (bzw. api.deepl.com für Pro)
    Authorization: DeepL-Auth-Key <key>
    -> {"character_count": 123456, "character_limit": 500000}

Per Doku-Recherche bestätigt (developers.deepl.com/docs/resources/usage-limits,
Stand Juli 2026): der Endpoint liefert KEIN Reset-/Verlängerungsdatum. Der
Nutzungszeitraum orientiert sich am DeepL-Anmeldedatum, nicht am
Kalendermonat - deshalb wird hier bevorzugt der LIVE-Wert verwendet (der
sich bei DeepL selbst automatisch zum richtigen Zeitpunkt zurücksetzt),
statt ein Reset-Datum zu erraten oder vom Nutzer abzufragen.

Der lokale, persistente Fallback-Zähler (local_accumulated_chars) deckt nur
den Fall ab, dass der Live-Check fehlschlägt (z.B. kein Netzwerk beim
Abfragen, obwohl der eigentliche Übersetzungsaufruf durchging) - er kann
driften, wenn derselbe DeepL-Account auch außerhalb dieser App genutzt
wird, und nutzt ein optionales, vom Nutzer eingetragenes Reset-Tag
(local_reset_day) nur als grobe Näherung für diesen Ausnahmefall.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

DEEPL_FREE_CHARACTER_LIMIT = 500_000


def _quota_file_path() -> Path:
    # Dieselbe Konvention wie credentials.py (~/.config/telegram-odt/) -
    # bewusst NICHT Qt QStandardPaths (ui/app.py::_config_dir), da dieses
    # Modul auch aus reinem Backend-/CLI-Kontext ohne QApplication genutzt
    # wird (siehe runner_schedule.py).
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "telegram-odt" / "deepl_quota.json"


@dataclass
class DeepLQuotaState:
    character_count: int = 0
    character_limit: int = DEEPL_FREE_CHARACTER_LIMIT
    source: str = "none"  # "api" (Live-Wert von DeepL) | "local" (Fallback) | "none" (noch nie geprüft)
    last_checked_iso: Optional[str] = None
    # Fallback-Zähler + optionales Reset-Tag - siehe Moduldoc.
    local_accumulated_chars: int = 0
    local_reset_day: Optional[int] = None  # 1-28
    local_period_start_iso: Optional[str] = None

    @property
    def remaining(self) -> int:
        return max(0, self.character_limit - self.character_count)


def load_quota_state() -> DeepLQuotaState:
    p = _quota_file_path()
    if not p.exists():
        return DeepLQuotaState()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return DeepLQuotaState()
        known = {f.name for f in fields(DeepLQuotaState)}
        return DeepLQuotaState(**{k: v for k, v in data.items() if k in known})
    except Exception:
        return DeepLQuotaState()


def save_quota_state(state: DeepLQuotaState) -> None:
    p = _quota_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")


def set_local_reset_day(day: Optional[int]) -> DeepLQuotaState:
    """Vom Nutzer eingetragener Tag des Monats, an dem der DeepL-
    Nutzungszeitraum beginnt - reine Fallback-Näherung für den lokalen
    Zähler, falls der Live-Check (siehe Moduldoc) mal nicht erreichbar ist."""
    state = load_quota_state()
    state.local_reset_day = day
    state.local_period_start_iso = None
    save_quota_state(state)
    return state


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_reset(after: datetime, reset_day: int) -> datetime:
    day = min(max(reset_day, 1), 28)
    candidate = after.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if candidate <= after:
        year, month = after.year, after.month + 1
        if month > 12:
            month = 1
            year += 1
        candidate = candidate.replace(year=year, month=month)
    return candidate


def _maybe_reset_local_period(state: DeepLQuotaState) -> None:
    if state.local_reset_day is None:
        return
    now = datetime.now(timezone.utc)
    if state.local_period_start_iso is None:
        state.local_period_start_iso = now.isoformat()
        return
    try:
        start = datetime.fromisoformat(state.local_period_start_iso)
    except ValueError:
        state.local_period_start_iso = now.isoformat()
        return
    if now >= _next_reset(start, state.local_reset_day):
        state.local_accumulated_chars = 0
        state.local_period_start_iso = now.isoformat()


def record_usage(chars_this_run: int, live_usage: Optional[Tuple[int, int]]) -> DeepLQuotaState:
    """Nach einem DeepL-Lauf aufrufen (einmal pro Lauf, NICHT pro Nachricht -
    live_usage kommt von einem einzigen GET /v2/usage-Aufruf, siehe
    DeepLProvider.get_usage() und dessen Verwendung in runner_schedule.py).

    live_usage=None, wenn der Live-Check fehlgeschlagen ist (z.B. Netzwerk-
    Problem beim Abfragen, obwohl die Übersetzung selbst durchging) - dann
    wird der lokale Fallback-Zähler als bestmögliche Schätzung verwendet.
    """
    state = load_quota_state()
    _maybe_reset_local_period(state)
    state.local_accumulated_chars += max(0, chars_this_run)

    if live_usage is not None:
        character_count, character_limit = live_usage
        state.character_count = character_count
        state.character_limit = character_limit
        state.source = "api"
    else:
        state.character_count = state.local_accumulated_chars
        state.character_limit = state.character_limit or DEEPL_FREE_CHARACTER_LIMIT
        state.source = "local"
    state.last_checked_iso = _now_iso()

    save_quota_state(state)
    return state


def would_exceed(state: DeepLQuotaState, additional_chars: int) -> bool:
    return state.character_count + max(0, additional_chars) > state.character_limit
