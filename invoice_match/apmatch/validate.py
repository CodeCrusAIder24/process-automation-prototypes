"""Arithmetic plausibility check for a parsed Invoice.

This is the guard rail against bad extraction (a regex grabbing the wrong
number) or LLM hallucination (a model inventing a total that "looks
right"): every invoice, however it was parsed - e-invoice XML, heuristic
regex or LLM - runs through the SAME two sanity checks before it is ever
matched against a PO. If the numbers on the invoice do not add up
internally, no rule further down the pipeline can be trusted, so this is
also, separately, a legitimate real-world AP exception ("Rechnung ist
rechnerisch falsch") - see sample invoice 18.
"""
from __future__ import annotations

from decimal import Decimal

from .formatting import fmt_eur
from .models import Finding, Invoice, round_eur


def validate_invoice(invoice: Invoice, total_abs_tolerance_eur: Decimal, route_to: str) -> list[Finding]:
    """Two checks, both compared against `total_abs_tolerance_eur`
    (rules.toml [tolerance].total_abs_eur):

    1. sum of line totals == stated Nettobetrag
    2. stated Nettobetrag * (1 + VAT rate) == stated Bruttobetrag

    Either mismatch produces one ARITHMETIC_MISMATCH finding (a single
    finding even if both checks fail, to avoid double-counting the same
    underlying problem in the money-at-risk KPI).
    """
    line_sum = round_eur(sum((l.line_total_eur for l in invoice.lines), Decimal("0.00")))
    net_diff = abs(line_sum - invoice.net_eur)

    expected_gross = round_eur(invoice.net_eur * (Decimal(1) + invoice.vat_rate_pct / Decimal(100)))
    gross_diff = abs(expected_gross - invoice.gross_eur)

    problems = []
    impact = Decimal("0.00")
    if net_diff > total_abs_tolerance_eur:
        problems.append(
            f"line totals sum to {fmt_eur(line_sum)} but the stated Nettobetrag is {fmt_eur(invoice.net_eur)} "
            f"(off by {fmt_eur(net_diff)})"
        )
        impact = max(impact, net_diff)
    if gross_diff > total_abs_tolerance_eur:
        problems.append(
            f"Nettobetrag + USt {invoice.vat_rate_pct}% should be {fmt_eur(expected_gross)} but the "
            f"stated Bruttobetrag is {fmt_eur(invoice.gross_eur)} (off by {fmt_eur(gross_diff)})"
        )
        impact = max(impact, gross_diff)

    if not problems:
        return []

    return [Finding(
        rule_id="ARITHMETIC_MISMATCH",
        severity="warning",
        message="Invoice arithmetic does not reconcile: " + "; ".join(problems) + ".",
        route_to=route_to,
        suggested_action="Request a corrected invoice from the supplier.",
        amount_impact_eur=impact,
        details={
            "line_sum": line_sum, "stated_net": invoice.net_eur, "net_diff": net_diff,
            "expected_gross": expected_gross, "stated_gross": invoice.gross_eur, "gross_diff": gross_diff,
        },
    )]
