"""Pick a single date from weekday_candidates (no imports from preview_range/date_anchor)."""

from __future__ import annotations

from datetime import date


def pick_nearest_weekday_option(
    options: list[dict[str, str]],
    today: date,
) -> str | None:
    """Nearest calendar date on or after today; else earliest in the list."""
    isos: list[str] = []
    for opt in options:
        raw = (opt.get("iso") or "")[:10]
        if len(raw) == 10:
            isos.append(raw)
    if not isos:
        return None
    isos.sort()
    for iso in isos:
        try:
            if date.fromisoformat(iso) >= today:
                return iso
        except ValueError:
            continue
    return isos[0]
