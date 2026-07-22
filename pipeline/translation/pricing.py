"""
Grobe Kostenschätzung für Übersetzungs-Provider.

Wichtig: Das sind reine Schätzwerte zum Zeitpunkt der Implementierung,
KEINE Live-Preisabfrage bei den Anbietern - Preise ändern sich häufig, wer
genaue Zahlen braucht, muss die jeweilige Anbieter-Abrechnung selbst
prüfen. Überschreibbar über config.yaml (translation.pricing.*).

Struktur: {provider: {model: {rate-Felder..., "checked_on": "YYYY-MM-DD"}}}
- pro Provider mehrere Modelle mit eigenen Preisen (wichtig für
  tokenbasierte Provider wie ChatGPT/Gemini, deren Preise stark vom
  konkreten Modell abhängen). Zeichenbasierte Provider (telegram/deepl/
  google) haben aktuell nur einen "default"-Eintrag. "checked_on"
  dokumentiert, wann der Wert zuletzt manuell verifiziert wurde - bei
  Preisänderungen bitte Wert UND Datum aktualisieren (siehe Kommentare
  unten für die jeweilige Quelle).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .base import TranslationResult

# Alle Preise in USD. "usd_per_million_chars" für zeichenbasierte Provider
# (DeepL/Google), "usd_per_million_..._tokens" für tokenbasierte (ChatGPT,
# Gemini - grobe Umrechnung Zeichen->Token siehe CHARS_PER_TOKEN_ESTIMATE/
# estimate_cost_from_chars() weiter unten, für die Vorschau VOR einem Lauf,
# bevor echte Tokenzahlen von der API vorliegen).
DEFAULT_PRICING: Dict[str, Dict[str, Dict[str, Any]]] = {
    "telegram": {
        "default": {"usd_per_million_chars": 0.0, "checked_on": "2026-07-22"},
    },
    "deepl": {
        "default": {"usd_per_million_chars": 20.0, "checked_on": "2026-07-22"},
    },
    "google": {
        "default": {"usd_per_million_chars": 20.0, "checked_on": "2026-07-22"},
    },
    "chatgpt": {
        # gpt-4o-mini ist das Default-Modell in chatgpt_provider.py
        # (_DEFAULT_MODEL) - Preise gelten für DIESES Modell, nicht für das
        # größere/teurere gpt-4o (das liegt eher bei ~$2.50/$10.00 pro 1M
        # Tokens). Bei Wechsel des Modells (config.yaml: translation.chatgpt.model)
        # hier einen passenden Eintrag ergänzen, sonst greift der
        # "default"-Fallback (siehe estimate_cost_usd) mit ggf. falschen Preisen.
        "gpt-4o-mini": {
            "usd_per_million_input_tokens": 0.15,
            "usd_per_million_output_tokens": 0.60,
            "checked_on": "2026-07-22",
        },
        "default": {
            "usd_per_million_input_tokens": 0.15,
            "usd_per_million_output_tokens": 0.60,
            "checked_on": "2026-07-22",
        },
    },
    "gemini": {
        # Noch KEIN eigener Gemini-TranslationProvider implementiert (siehe
        # get_provider() in pipeline/translation/__init__.py - aktuell nur
        # deepl/google/chatgpt) - diese Preistabelle ist vorbereitet für den
        # Fall, dass ein Gemini-Provider ergänzt wird, wird aber aktuell von
        # keinem Code-Pfad genutzt. Modellnamen/Preise laut Google AI-Preisliste.
        "gemini-2.5-flash-lite": {
            "usd_per_million_input_tokens": 0.10,
            "usd_per_million_output_tokens": 0.40,
            "checked_on": "2026-07-22",
        },
        "gemini-2.5-flash": {
            "usd_per_million_input_tokens": 0.30,
            "usd_per_million_output_tokens": 2.50,
            "checked_on": "2026-07-22",
        },
        "default": {
            "usd_per_million_input_tokens": 0.10,
            "usd_per_million_output_tokens": 0.40,
            "checked_on": "2026-07-22",
        },
    },
}

# Grobe Faustregel für die Zeichen->Token-Umrechnung bei der Vorschau VOR
# einem Lauf (siehe estimate_cost_from_chars) - NICHT exakt, tatsächliche
# Tokenisierung hängt vom Modell/der Sprache ab. Nur für tokenbasierte
# Provider (chatgpt/gemini) relevant.
CHARS_PER_TOKEN_ESTIMATE = 4


def _resolve_rates(provider: str, model: Optional[str], table: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    provider_table = table.get(provider, {})
    if model and model in provider_table:
        return provider_table[model]
    if "default" in provider_table:
        return provider_table["default"]
    return next(iter(provider_table.values()), {})


def is_token_based_provider(provider: str, pricing: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None) -> bool:
    """True, wenn IRGENDEIN Modell-Eintrag dieses Providers auf Token-Preisen
    beruht (chatgpt/gemini) statt auf Zeichen-Preisen (deepl/google/telegram)."""
    table = pricing or DEFAULT_PRICING
    return any(
        "usd_per_million_input_tokens" in rates or "usd_per_million_output_tokens" in rates
        for rates in table.get(provider, {}).values()
    )


def estimate_cost_usd(
    provider: str,
    *,
    model: Optional[str] = None,
    char_count: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    pricing: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> float:
    table = pricing or DEFAULT_PRICING
    rates = _resolve_rates(provider, model, table)
    cost = 0.0
    if char_count and "usd_per_million_chars" in rates:
        cost += char_count * rates["usd_per_million_chars"] / 1_000_000
    if input_tokens and "usd_per_million_input_tokens" in rates:
        cost += input_tokens * rates["usd_per_million_input_tokens"] / 1_000_000
    if output_tokens and "usd_per_million_output_tokens" in rates:
        cost += output_tokens * rates["usd_per_million_output_tokens"] / 1_000_000
    return cost


def estimate_cost_from_chars(
    provider: str,
    char_count: int,
    *,
    model: Optional[str] = None,
    pricing: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> Tuple[float, bool]:
    """Schätzt Kosten rein aus einer Zeichenzahl - für die Vorschau VOR
    einem Lauf (siehe ui/app.py::_on_char_preview_finished), bevor echte
    Tokenzahlen von der API vorliegen.

    Bei zeichenbasierten Providern (deepl/google/telegram) direkt über
    estimate_cost_usd(). Bei tokenbasierten Providern (chatgpt/gemini) über
    eine grobe Tokenschätzung (~CHARS_PER_TOKEN_ESTIMATE Zeichen/Token) -
    dabei werden Input- UND Output-Tokens beide grob mit derselben
    geschätzten Token-Zahl angesetzt (Annahme: übersetzter Text ist ähnlich
    lang wie das Original; ignoriert man Output komplett, würde man die
    Kosten deutlich unterschätzen, da Output-Preise oft höher als
    Input-Preise sind).

    Rückgabe: (geschätzte_kosten_usd, is_approximate) - is_approximate=True
    signalisiert der UI, den "grobe Schätzung"-Hinweis anzuzeigen.
    """
    table = pricing or DEFAULT_PRICING
    if is_token_based_provider(provider, table):
        approx_tokens = max(0, char_count) // CHARS_PER_TOKEN_ESTIMATE
        cost = estimate_cost_usd(
            provider, model=model, input_tokens=approx_tokens, output_tokens=approx_tokens, pricing=table,
        )
        return cost, True
    cost = estimate_cost_usd(provider, model=model, char_count=char_count, pricing=table)
    return cost, False


@dataclass
class _ProviderTotals:
    calls: int = 0
    char_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class TranslationCostTracker:
    """Sammelt TranslationResult-Objekte über einen ganzen Lauf hinweg,
    getrennt pro Provider (falls mehrere gemischt genutzt würden)."""
    _totals: Dict[str, _ProviderTotals] = field(default_factory=dict)

    def add(self, result: TranslationResult) -> None:
        t = self._totals.setdefault(result.provider, _ProviderTotals())
        t.calls += 1
        t.char_count += result.char_count
        t.input_tokens += result.input_tokens or 0
        t.output_tokens += result.output_tokens or 0
        t.estimated_cost_usd += result.estimated_cost_usd

    @property
    def total_cost_usd(self) -> float:
        return sum(t.estimated_cost_usd for t in self._totals.values())

    def has_data(self) -> bool:
        return bool(self._totals)

    def provider_totals(self) -> List[Tuple[str, int, int, int, int, float]]:
        """(provider, calls, char_count, input_tokens, output_tokens,
        estimated_cost_usd) je Provider mit calls > 0 - liefert Rohdaten für
        eine UI-seitige, über Qt-i18n übersetzbare Anzeige (siehe
        ui/app.py::_on_worker_finished). summary_lines() bleibt für Log-/
        Konsolenausgabe reserviert (bewusst nicht übersetzt - reiner
        Backend-String ohne Qt-Kontext, siehe _notify in runner_schedule.py)."""
        return [
            (provider, t.calls, t.char_count, t.input_tokens, t.output_tokens, t.estimated_cost_usd)
            for provider, t in sorted(self._totals.items())
            if t.calls > 0
        ]

    def summary_lines(self) -> List[str]:
        """Menschenlesbare Zeilen, klar als Schätzung gekennzeichnet."""
        lines: List[str] = []
        for provider, t in sorted(self._totals.items()):
            if t.calls == 0:
                continue
            detail = f"{t.calls} Nachrichten"
            if t.char_count:
                detail += f", {t.char_count} Zeichen"
            if t.input_tokens or t.output_tokens:
                detail += f", {t.input_tokens}+{t.output_tokens} Tokens (in/out)"
            lines.append(
                f"{provider}: {detail}, geschätzte Kosten ~${t.estimated_cost_usd:.4f} (Schätzung, keine Live-Preisabfrage)"
            )
        return lines
