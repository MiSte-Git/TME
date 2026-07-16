"""
Grobe Kostenschätzung für Übersetzungs-Provider.

Wichtig: Das sind reine Schätzwerte zum Zeitpunkt der Implementierung,
KEINE Live-Preisabfrage bei den Anbietern. Preise ändern sich; wer genaue
Zahlen braucht, muss die jeweilige Anbieter-Abrechnung selbst prüfen.
Überschreibbar über config.yaml (translation.pricing.*).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .base import TranslationResult

# Alle Preise in USD. "usd_per_million_chars" für zeichenbasierte Provider
# (DeepL/Google), "usd_per_million_..._tokens" für tokenbasierte (ChatGPT).
DEFAULT_PRICING: Dict[str, Dict[str, float]] = {
    "telegram": {"usd_per_million_chars": 0.0},
    "deepl": {"usd_per_million_chars": 20.0},
    "google": {"usd_per_million_chars": 20.0},
    "chatgpt": {"usd_per_million_input_tokens": 150.0, "usd_per_million_output_tokens": 600.0},
}


def estimate_cost_usd(
    provider: str,
    *,
    char_count: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    pricing: Dict[str, Dict[str, float]] | None = None,
) -> float:
    table = pricing or DEFAULT_PRICING
    rates = table.get(provider, {})
    cost = 0.0
    if char_count and "usd_per_million_chars" in rates:
        cost += char_count * rates["usd_per_million_chars"] / 1_000_000
    if input_tokens and "usd_per_million_input_tokens" in rates:
        cost += input_tokens * rates["usd_per_million_input_tokens"] / 1_000_000
    if output_tokens and "usd_per_million_output_tokens" in rates:
        cost += output_tokens * rates["usd_per_million_output_tokens"] / 1_000_000
    return cost


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
