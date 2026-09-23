"""Before/after KPIs: what this batch actually achieved, plus a clearly-
labelled extrapolation to a full month/year using the assumptions in
rules.toml [kpi]. Every formula lives here, once, with a docstring, and the
same numbers are what report.py prints in the console and renders in
out/report.html - so the assumptions next to a number in the report are
never out of sync with how it was computed.
"""
from __future__ import annotations

from decimal import Decimal

from .match import MONEY_AT_RISK_RULES
from .models import MatchResult, round_eur

PRODUCTIVE_HOURS_PER_MONTH = Decimal("140")  # assumed productive hours per FTE per month

# The "9 manual steps" a clerk does today per invoice, one per roughly the
# manual_minutes_per_invoice=9 assumption in rules.toml - deliberately a
# literal step-per-minute mapping so the BEFORE strip in the report reads
# as "this is where the 9 minutes go", not an arbitrary illustration.
BEFORE_STEPS = [
    "Open mail / download PDF attachment",
    "Open ERP and create a new invoice record",
    "Type in supplier, invoice number, date",
    "Look up the purchase order",
    "Look up the goods receipt for that PO",
    "Compare quantities line by line",
    "Compare unit prices line by line",
    "Check VAT and totals arithmetic",
    "Post the invoice / file the paper copy",
]
AFTER_STEPS_TOUCHLESS = [
    "System extracts, matches and posts automatically - no human step.",
]
AFTER_STEPS_EXCEPTION = [
    "System pre-analyses the exception and drafts the supplier/store query",
    "Human reviews the pre-analysis and payable-amount suggestion",
    "Human approves, edits, or escalates the drafted action",
]


def clean_invoice_step_counts() -> tuple[int, int, int]:
    """(steps_before, human_steps_after, steps_removed) for a single clean
    (touchless) invoice - the single source of truth for the "N of M manual
    steps removed" claim used in the report and the README, so the two can
    never drift apart the way they once did (report said "9 of 9", README
    said "8 of 9").

    steps_before = len(BEFORE_STEPS) - every manual step a clerk does today.
    human_steps_after = 0, always - not len(AFTER_STEPS_TOUCHLESS), which
      lists one entry but it is the SYSTEM doing the work ("no human step"
      is explicit in its own text), not a human step that merely shrank.
    steps_removed = steps_before - human_steps_after, i.e. all of them.
    """
    steps_before = len(BEFORE_STEPS)
    human_steps_after = 0
    return steps_before, human_steps_after, steps_before - human_steps_after


def money_at_risk_by_rule(results: list[MatchResult]) -> dict[str, Decimal]:
    """Sum of Finding.amount_impact_eur, grouped by rule_id, restricted to
    the rules that represent a quantified EUR amount at risk (see
    match.MONEY_AT_RISK_RULES). Duplicate detection (exact + fuzzy) is
    merged into one "DUPLICATE" bucket for the report."""
    by_rule: dict[str, Decimal] = {}
    for r in results:
        for f in r.findings:
            if f.rule_id not in MONEY_AT_RISK_RULES:
                continue
            bucket = "DUPLICATE" if f.rule_id.startswith("DUPLICATE") else f.rule_id
            by_rule[bucket] = by_rule.get(bucket, Decimal("0.00")) + f.amount_impact_eur
    return by_rule


def batch_kpis(results: list[MatchResult]) -> dict:
    """Facts about THIS batch (no extrapolation) - the numbers the report's
    stat row and worklist are built from."""
    total = len(results)
    by_decision = {"APPROVED": 0, "EXCEPTION": 0, "HOLD": 0, "BLOCKED": 0}
    for r in results:
        by_decision[r.decision] = by_decision.get(r.decision, 0) + 1

    touchless_rate_pct = round(100 * by_decision["APPROVED"] / total, 1) if total else 0.0

    money_by_rule = money_at_risk_by_rule(results)
    money_at_risk_total = round_eur(sum(money_by_rule.values(), Decimal("0.00")))

    # Skonto captured = discount kept for invoices that are APPROVED (i.e.
    # will actually be paid) AND whose deadline is still reachable as of
    # --today (skonto.compute_skonto already returns 0 once it has passed).
    skonto_captured_eur = round_eur(sum(
        (r.skonto_eur for r in results if r.decision == "APPROVED"), Decimal("0.00")
    ))

    return {
        "total_invoices": total,
        "approved": by_decision["APPROVED"],
        "exceptions": by_decision["EXCEPTION"],
        "holds": by_decision["HOLD"],
        "blocked": by_decision["BLOCKED"],
        "touchless_rate_pct": touchless_rate_pct,
        "money_at_risk_by_rule": {k: str(v) for k, v in money_by_rule.items()},
        "money_at_risk_total_eur": str(money_at_risk_total),
        "skonto_captured_eur": str(skonto_captured_eur),
    }


def extrapolate(batch: dict, kpi_cfg: dict) -> dict:
    """Project the batch's touchless/exception mix onto a full month/year
    using rules.toml [kpi]. Every number here is explicitly an ESTIMATE
    built from assumptions the report shows next to it - never presented
    as measured fact.

    human_share  = (exceptions + blocked) / total_invoices_in_batch
                   (HOLD is excluded: it is an automatic re-check, not a
                   human review step, same as a touchless APPROVED)

    hours_before = invoices_per_month
                   * [ (1 - human_share) * manual_minutes_per_invoice
                       + human_share * exception_minutes_manual ] / 60
                   Today, EVERY invoice - touchless-share and human_share
                   alike - gets the same manual_minutes_per_invoice of
                   initial handling (nobody knows in advance which one will
                   turn out clean); the human_share that today needs full
                   manual investigation on top of that is charged at the
                   heavier exception_minutes_manual instead.

    hours_after  = invoices_per_month * human_share * exception_minutes_assisted / 60
                   After automation, the touchless share costs 0 human
                   minutes; only the human_share still needs a person, and
                   with pre-analysis + a drafted action that takes
                   exception_minutes_assisted instead of exception_minutes_manual.

    hours_saved    = hours_before - hours_after
    fte_equivalent = hours_saved / 140 productive hours per month
    cost_saved_eur = hours_saved * hourly_cost_eur

    skonto_recovered_per_year:
      avg_potential_skonto = mean, over batch invoices whose supplier has
        skonto terms, of (payable_eur * skonto_pct/100) - i.e. what the
        discount WOULD be if always paid in time, regardless of whether
        this batch's run date actually still allows it.
      recovered_per_year = avg_potential_skonto * invoices_per_month * 12
                            * skonto_eligible_share_lost_today
      (the share of skonto-eligible invoices that miss the discount today
      because manual processing is too slow - an estimate from rules.toml,
      not measured in this batch.)
    """
    invoices_per_month = Decimal(str(kpi_cfg["invoices_per_month"]))
    manual_minutes = Decimal(str(kpi_cfg["manual_minutes_per_invoice"]))
    exception_minutes_manual = Decimal(str(kpi_cfg["exception_minutes_manual"]))
    exception_minutes_assisted = Decimal(str(kpi_cfg["exception_minutes_assisted"]))
    hourly_cost = Decimal(str(kpi_cfg["hourly_cost_eur"]))

    total = Decimal(batch["total_invoices"]) if batch["total_invoices"] else Decimal(1)
    human_share = (Decimal(batch["exceptions"]) + Decimal(batch["blocked"])) / total
    touchless_share = Decimal(1) - human_share

    hours_before = invoices_per_month * (
        touchless_share * manual_minutes + human_share * exception_minutes_manual
    ) / Decimal(60)
    hours_after = invoices_per_month * human_share * exception_minutes_assisted / Decimal(60)
    hours_saved = hours_before - hours_after
    fte_equivalent = hours_saved / PRODUCTIVE_HOURS_PER_MONTH
    cost_saved_eur = round_eur(hours_saved * hourly_cost)

    return {
        "invoices_per_month": str(invoices_per_month),
        "human_share_pct": str(round(human_share * 100, 1)),
        "hours_before_per_month": str(round(hours_before, 1)),
        "hours_after_per_month": str(round(hours_after, 1)),
        "hours_saved_per_month": str(round(hours_saved, 1)),
        "fte_equivalent": str(round(fte_equivalent, 2)),
        "cost_saved_eur_per_month": str(cost_saved_eur),
        "cost_saved_eur_per_year": str(round_eur(cost_saved_eur * Decimal(12))),
    }


def skonto_recovered_per_year(results: list[MatchResult], kpi_cfg: dict) -> str:
    """See extrapolate()'s docstring for the formula; split out as its own
    function because it needs the per-invoice results, not just the batch
    summary dict."""
    eligible = [r for r in results if r.supplier and r.supplier.skonto_pct > 0]
    if not eligible:
        return "0.00"
    potentials = [round_eur(r.payable_eur * r.supplier.skonto_pct / Decimal(100)) for r in eligible]
    avg_potential = sum(potentials, Decimal("0.00")) / Decimal(len(potentials))

    invoices_per_month = Decimal(str(kpi_cfg["invoices_per_month"]))
    lost_share = Decimal(str(kpi_cfg["skonto_eligible_share_lost_today"]))
    recovered = avg_potential * invoices_per_month * Decimal(12) * lost_share
    return str(round_eur(recovered))
