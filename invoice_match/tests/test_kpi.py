"""Sanity checks on the KPI formulas (kpi.py) with small hand-built
MatchResults - not a re-check of the full batch (see test_end_to_end.py)."""
from decimal import Decimal

from apmatch import kpi
from apmatch.models import Finding, Invoice, InvoiceLine, MatchResult, Supplier


def _result(decision, findings=None, skonto_eur="0.00", supplier_skonto_pct="2.0"):
    inv = Invoice(source_file="t.txt", supplier_vat_id="DE1", invoice_number="RE-1",
                  invoice_date="2026-09-15", po_number="PO-1", iban="DE00",
                  net_eur=Decimal("100.00"), vat_rate_pct=Decimal("19"), vat_amount_eur=Decimal("19.00"),
                  gross_eur=Decimal("119.00"), lines=[InvoiceLine("X", "x", Decimal(1), Decimal("100"), Decimal("100"))])
    supplier = Supplier("S01", "Test GmbH", "DE1", "DE00", Decimal(supplier_skonto_pct), 10, 30)
    return MatchResult(invoice=inv, supplier=supplier, findings=findings or [], decision=decision,
                        route_to="-", payable_eur=Decimal("119.00"), skonto_eur=Decimal(skonto_eur),
                        skonto_deadline=None, pay_by=None, processing_path="auto" if decision in ("APPROVED", "HOLD") else "manual")


def test_batch_kpis_counts_and_touchless_rate():
    results = [_result("APPROVED"), _result("APPROVED"), _result("EXCEPTION"), _result("BLOCKED")]
    batch = kpi.batch_kpis(results)
    assert batch["total_invoices"] == 4
    assert batch["approved"] == 2
    assert batch["exceptions"] == 1
    assert batch["blocked"] == 1
    assert batch["touchless_rate_pct"] == 50.0


def test_money_at_risk_sums_only_whitelisted_rules():
    findings = [
        Finding("PRICE_DEVIATION", "warning", "m", "r", "a", amount_impact_eur=Decimal("10.00")),
        Finding("PO_UNKNOWN", "warning", "m", "r", "a", amount_impact_eur=Decimal("999.00")),
    ]
    results = [_result("EXCEPTION", findings=findings)]
    batch = kpi.batch_kpis(results)
    assert batch["money_at_risk_total_eur"] == "10.00"
    assert "PO_UNKNOWN" not in batch["money_at_risk_by_rule"]


def test_duplicate_exact_and_fuzzy_merged_into_one_bucket():
    findings = [
        Finding("DUPLICATE_EXACT", "critical", "m", "r", "a", amount_impact_eur=Decimal("50.00")),
    ]
    results = [_result("BLOCKED", findings=findings)]
    batch = kpi.batch_kpis(results)
    assert batch["money_at_risk_by_rule"] == {"DUPLICATE": "50.00"}


def test_skonto_captured_only_counts_approved():
    results = [_result("APPROVED", skonto_eur="5.00"), _result("EXCEPTION", skonto_eur="7.00")]
    batch = kpi.batch_kpis(results)
    assert batch["skonto_captured_eur"] == "5.00"


def test_extrapolate_hours_before_after():
    batch = {"total_invoices": 10, "exceptions": 2, "blocked": 1, "holds": 1, "approved": 6}
    kpi_cfg = {"invoices_per_month": 400, "manual_minutes_per_invoice": 9,
               "exception_minutes_assisted": 12, "hourly_cost_eur": 45,
               "skonto_eligible_share_lost_today": 0.40, "exception_minutes_manual": 25}
    extrap = kpi.extrapolate(batch, kpi_cfg)
    # human_share = (2+1)/10 = 0.3, touchless_share = 0.7
    # hours_before = 400*(0.7*9 + 0.3*25)/60 = 400*13.8/60 = 92.0
    assert extrap["hours_before_per_month"] == "92.0"
    # hours_after = 400*0.3*12/60 = 24.0
    assert extrap["hours_after_per_month"] == "24.0"
    assert extrap["hours_saved_per_month"] == "68.0"
    assert extrap["human_share_pct"] == "30.0"


def test_skonto_recovered_per_year_zero_when_no_eligible_suppliers():
    inv = Invoice(source_file="t.txt", supplier_vat_id="DE1", invoice_number="RE-1", invoice_date="2026-09-15",
                  po_number="PO-1", iban="DE00", net_eur=Decimal("100"), vat_rate_pct=Decimal("19"),
                  vat_amount_eur=Decimal("19"), gross_eur=Decimal("119"), lines=[])
    supplier = Supplier("S04", "No Skonto GmbH", "DE1", "DE00", Decimal("0.0"), 0, 30)
    result = MatchResult(invoice=inv, supplier=supplier, findings=[], decision="APPROVED",
                          route_to="-", payable_eur=Decimal("119.00"), skonto_eur=Decimal("0.00"),
                          skonto_deadline=None, pay_by=None, processing_path="auto")
    kpi_cfg = {"invoices_per_month": 400, "skonto_eligible_share_lost_today": 0.40}
    assert kpi.skonto_recovered_per_year([result], kpi_cfg) == "0.00"


def test_clean_invoice_step_counts_is_the_single_source_of_truth():
    """Guards against the report/README drift this once had ("9 of 9" vs
    "8 of 9"): human_steps_after must be 0 (AFTER_STEPS_TOUCHLESS's one
    entry is the system, not a human), so ALL steps are removed, not
    len(BEFORE_STEPS) - len(AFTER_STEPS_TOUCHLESS)."""
    before, after, removed = kpi.clean_invoice_step_counts()
    assert before == len(kpi.BEFORE_STEPS) == 9
    assert after == 0
    assert removed == before == 9
