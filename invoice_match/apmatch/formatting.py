"""Shared formatting helpers so every user-facing surface - console,
report.html, worklist.csv, finding messages, supplier drafts - renders
money and dates the same way. out/results.json is the one place that
deliberately keeps plain machine-readable decimal strings and ISO dates
(see models.py's to_dict methods); everywhere a human reads the number, it
goes through here instead.
"""
from __future__ import annotations

from decimal import Decimal


def fmt_eur(value) -> str:
    """1234.56 -> '1.234,56 EUR' (German grouping/decimal convention)."""
    d = Decimal(str(value))
    neg = d < 0
    d = abs(d)
    s = f"{d:.2f}"
    intpart, dec = s.split(".")
    groups = []
    while len(intpart) > 3:
        groups.insert(0, intpart[-3:])
        intpart = intpart[:-3]
    groups.insert(0, intpart)
    out = ".".join(groups) + "," + dec + " EUR"
    return ("-" if neg else "") + out


def fmt_de_date(iso: str | None) -> str:
    """'2026-09-24' -> '24.09.2026'. Returns '-' for None/empty."""
    if not iso:
        return "-"
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


def fmt_de_number(value, digits: int = 1) -> str:
    """German decimal formatting for plain numbers - percentages, FTE
    counts, hours - anything that isn't money (use fmt_eur for that, which
    always adds the EUR suffix): 6.0 -> '6,0', 0.49 -> '0,49' (digits=2),
    63.5 -> '63,5', 1234 -> '1.234' (digits=0, thousands-grouped like
    fmt_eur). Accepts int/float/Decimal/numeric-string alike."""
    if digits == 0:
        return f"{int(round(float(value))):,}".replace(",", ".")
    return f"{float(value):.{digits}f}".replace(".", ",")


def fmt_pct(value, digits: int = 1) -> str:
    """German percentage: 6.0 -> '6,0 %', 33.3 -> '33,3 %' (regular space,
    not a non-breaking one - callers embedding this in HTML that wants
    &nbsp; instead use fmt_de_number directly and add the entity themselves,
    as most of the report's own markup already does)."""
    return f"{fmt_de_number(value, digits)} %"
