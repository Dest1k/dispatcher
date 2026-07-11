"""Versioned model capability catalog.

Records what we can actually source about a model — context window, pricing,
effort options — together with WHEN it was verified and from WHERE, and marks
stale entries. Models we cannot verify are reported as "no data" rather than
presented with guessed facts. User overrides (price/effort) live in the provider
config; this catalog is provenance, not a source of hidden defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

STALE_AFTER_DAYS = 120


@dataclass
class CatalogEntry:
    model_id: str
    context_window: int
    price_in: float
    price_out: float
    effort_options: list[str]
    verified_at: str          # ISO date
    source: str
    note: str = ""

    def age_days(self, today: date | None = None) -> int:
        today = today or date.today()
        try:
            v = datetime.strptime(self.verified_at, "%Y-%m-%d").date()
        except ValueError:
            return 10_000
        return (today - v).days

    def is_stale(self, today: date | None = None) -> bool:
        return self.age_days(today) > STALE_AFTER_DAYS


# Only entries we can actually source are listed. Unknown/future model ids
# intentionally have NO entry, so the UI says "no catalog data — verify with the
# provider" instead of fabricating numbers.
_CATALOG: dict[str, CatalogEntry] = {
    "claude-opus-4-8": CatalogEntry(
        model_id="claude-opus-4-8", context_window=1_000_000,
        price_in=5.0, price_out=25.0,
        effort_options=["low", "medium", "high", "xhigh", "max"],
        verified_at="2026-06-24",
        source="Anthropic platform docs (cached in the claude-api skill)",
        note="1M context; adaptive thinking; effort levels."),
}


def get(model_id: str) -> CatalogEntry | None:
    return _CATALOG.get((model_id or "").strip())


def describe(model_id: str, today: date | None = None) -> str:
    """A short, honest one-line description for the UI."""
    entry = get(model_id)
    if entry is None:
        return "нет данных в каталоге — проверь параметры у провайдера"
    stale = " · ⚠ данные устарели, перепроверь" if entry.is_stale(today) else ""
    ctx = (f"{entry.context_window // 1000}K" if entry.context_window < 1_000_000
           else f"{entry.context_window // 1_000_000}M")
    return (f"контекст {ctx} · ${entry.price_in}/{entry.price_out} за 1M · "
            f"проверено {entry.verified_at} ({entry.source}){stale}")
