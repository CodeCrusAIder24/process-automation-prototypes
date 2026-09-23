"""Skonto (early-payment discount) deadline and amount.

German B2B payment terms commonly offer a small discount (Skonto, typically
1.5-3%) for paying well before the net due date - e.g. "2% Skonto bei
Zahlung innerhalb 10 Tagen, sonst netto 30 Tage". Missing that window is
pure margin left on the table, which is why it is one of the two headline
money metrics in the KPI dashboard (kpi.py).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .models import Supplier, round_eur


def parse_iso(value: str) -> date:
    y, m, d = value.split("-")
    return date(int(y), int(m), int(d))


def skonto_at_risk(deadline_iso: str | None, today: date, risk_days: int) -> bool:
    """True if the skonto deadline falls within `risk_days` of today
    (inclusive), i.e. it will be missed unless this invoice is prioritised
    right now. Used by match.rule_gr_missing to flag urgency on HOLD."""
    if deadline_iso is None:
        return False
    delta = (parse_iso(deadline_iso) - today).days
    return 0 <= delta <= risk_days


def compute_skonto(
    payable_eur: Decimal, supplier: Supplier | None, invoice_date: str, today: date
) -> tuple[Decimal, str | None, str | None]:
    """Returns (skonto_eur, skonto_deadline_iso, pay_by_iso).

    - skonto_eur is 0 if the supplier has no skonto terms, or if the
      deadline has already passed relative to `today` (the discount was
      missed - still money-at-risk-adjacent, but not "capturable" in this
      batch).
    - pay_by is the date AP should actually pay: the skonto deadline while
      it is still reachable, otherwise the plain net-due date.
    - skonto_deadline is returned even when it has passed, so the report
      can still show "deadline was DD.MM., missed" instead of blank.
    """
    if supplier is None:
        return Decimal("0.00"), None, None

    net_due = (parse_iso(invoice_date) + timedelta(days=supplier.net_days)).isoformat()

    if supplier.skonto_pct <= 0 or supplier.skonto_days <= 0:
        return Decimal("0.00"), None, net_due

    deadline = (parse_iso(invoice_date) + timedelta(days=supplier.skonto_days)).isoformat()
    still_reachable = parse_iso(deadline) >= today

    if still_reachable:
        skonto_eur = round_eur(payable_eur * supplier.skonto_pct / Decimal(100))
        return skonto_eur, deadline, deadline

    return Decimal("0.00"), deadline, net_due
