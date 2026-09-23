"""Each match.py rule tested in isolation with small hand-built objects,
plus the decision-precedence logic. Uses the real rules.toml so the tests
exercise the same tolerances the demo run does."""
import tomllib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from apmatch import match, skonto
from apmatch.models import Finding, GoodsReceipt, Invoice, InvoiceLine, POLine, PostedInvoice, Supplier

RULES_PATH = Path(__file__).resolve().parent.parent / "rules.toml"


@pytest.fixture
def cfg():
    with open(RULES_PATH, "rb") as f:
        return tomllib.load(f)


@pytest.fixture
def supplier():
    return Supplier(supplier_id="S01", name="Gartenbedarf Nord GmbH", vat_id="DE118743205",
                     iban="DE12500105170648512345", skonto_pct=Decimal("2.0"), skonto_days=10, net_days=30)


def _line(sku="X1", desc="Widget", qty="10", price="5.00"):
    return InvoiceLine(sku=sku, description=desc, qty=Decimal(qty), unit_price_eur=Decimal(price),
                        line_total_eur=Decimal(qty) * Decimal(price))


def _invoice(vat="DE118743205", po="PO-1", iban="DE12500105170648512345", lines=None, invnum="RE-9",
             date="2026-09-15", gross="50.00"):
    lines = lines if lines is not None else [_line()]
    return Invoice(source_file="t.txt", supplier_vat_id=vat, invoice_number=invnum, invoice_date=date,
                    po_number=po, iban=iban, net_eur=Decimal("50.00"), vat_rate_pct=Decimal("19"),
                    vat_amount_eur=Decimal("9.50"), gross_eur=Decimal(gross), lines=lines)


def _po_line(sku="X1", qty="10", price="5.00"):
    return POLine(po_number="PO-1", supplier_id="S01", store="HH1", line=1, sku=sku,
                  description="Widget", qty_ordered=Decimal(qty), unit_price_eur=Decimal(price),
                  order_date="2026-09-01")


def _gr(sku="X1", qty="10"):
    return GoodsReceipt(gr_id="GR-1", po_number="PO-1", sku=sku, qty_received=Decimal(qty),
                         received_date="2026-09-10", store="HH1")


# --- supplier / PO existence -------------------------------------------------

def test_rule_supplier_unknown_fires(cfg):
    inv = _invoice()
    findings = match.rule_supplier_unknown(inv, None, cfg)
    assert len(findings) == 1 and findings[0].rule_id == "SUPPLIER_UNKNOWN"
    assert findings[0].severity == "warning"


def test_rule_supplier_unknown_silent_when_known(cfg, supplier):
    assert match.rule_supplier_unknown(_invoice(), supplier, cfg) == []


def test_rule_po_unknown_when_po_missing_from_master(cfg):
    inv = _invoice(po="PO-DOES-NOT-EXIST")
    findings = match.rule_po_unknown(inv, None, cfg)
    assert len(findings) == 1 and findings[0].rule_id == "PO_UNKNOWN"


def test_rule_po_unknown_when_no_po_reference_at_all(cfg):
    inv = _invoice(po=None)
    findings = match.rule_po_unknown(inv, None, cfg)
    assert len(findings) == 1 and findings[0].rule_id == "PO_UNKNOWN"


def test_rule_po_unknown_silent_when_po_found(cfg):
    assert match.rule_po_unknown(_invoice(), [_po_line()], cfg) == []


# --- price tolerance ----------------------------------------------------------

def test_price_within_tolerance_is_info_not_exception(cfg):
    inv = _invoice(lines=[_line(price="5.03")])  # 0.6% above PO price 5.00
    findings = match.rule_price_tolerance(inv, [_po_line()], cfg)
    assert len(findings) == 1
    assert findings[0].rule_id == "PRICE_WITHIN_TOLERANCE"
    assert findings[0].severity == "info"


def test_price_above_tolerance_is_warning(cfg):
    inv = _invoice(lines=[_line(price="5.35")])  # 7% above PO price 5.00
    findings = match.rule_price_tolerance(inv, [_po_line()], cfg)
    assert len(findings) == 1
    assert findings[0].rule_id == "PRICE_DEVIATION"
    assert findings[0].severity == "warning"
    assert findings[0].amount_impact_eur == Decimal("3.50")  # (5.35-5.00) * 10


def test_price_exact_match_no_finding(cfg):
    assert match.rule_price_tolerance(_invoice(), [_po_line()], cfg) == []


# --- short delivery / goods receipt -------------------------------------------

def test_short_delivery_detected(cfg):
    inv = _invoice(lines=[_line(qty="10")])
    findings = match.rule_short_delivery(inv, [_po_line()], [_gr(qty="8")], cfg)
    assert len(findings) == 1
    assert findings[0].rule_id == "QTY_SHORT"
    assert findings[0].amount_impact_eur == Decimal("10.00")  # 2 units short * 5.00


def test_short_delivery_silent_when_fully_received(cfg):
    assert match.rule_short_delivery(_invoice(), [_po_line()], [_gr(qty="10")], cfg) == []


def test_rule_gr_missing_fires_as_hold(cfg, supplier):
    findings = match.rule_gr_missing(_invoice(), [], supplier, cfg, date(2026, 9, 23))
    assert len(findings) == 1
    assert findings[0].rule_id == "GR_NOT_POSTED"
    assert findings[0].hold is True


def test_rule_gr_missing_silent_when_gr_present(cfg, supplier):
    assert match.rule_gr_missing(_invoice(), [_gr()], supplier, cfg, date(2026, 9, 23)) == []


def test_rule_gr_missing_includes_skonto_urgency_note(cfg, supplier):
    """Matches sample invoice 14: S01 (2% / 10 days), invoice_date
    2026-09-14 -> deadline 2026-09-24, which is 1 day after --today
    2026-09-23 (within the 3-day risk window) - the message must spell out
    the real deadline/discount, computed, not hardcoded."""
    inv = _invoice(date="2026-09-14", gross="464.70")
    findings = match.rule_gr_missing(inv, [], supplier, cfg, date(2026, 9, 23))
    assert len(findings) == 1
    msg = findings[0].message
    assert "24.09.2026" in msg
    assert "in 1 day" in msg
    assert "9,29 EUR" in msg  # 464.70 * 2% = 9.294 -> 9.29
    assert findings[0].details["skonto_deadline"] == "2026-09-24"
    assert findings[0].details["skonto_eur"] == Decimal("9.29")
    assert findings[0].details["days_left"] == 1


def test_rule_gr_missing_no_urgency_note_when_deadline_far_away(cfg, supplier):
    inv = _invoice(date="2026-08-01", gross="464.70")  # deadline long past, no discount left to protect
    findings = match.rule_gr_missing(inv, [], supplier, cfg, date(2026, 9, 23))
    assert "Skonto deadline" not in findings[0].message


def test_rule_gr_missing_says_today_when_deadline_is_today(cfg, supplier):
    inv = _invoice(date="2026-09-13", gross="464.70")  # deadline 2026-09-23 == today
    findings = match.rule_gr_missing(inv, [], supplier, cfg, date(2026, 9, 23))
    assert "(today)" in findings[0].message


def test_skonto_at_risk_flag():
    assert skonto.skonto_at_risk("2026-09-24", __import__("datetime").date(2026, 9, 23), 3) is True
    assert skonto.skonto_at_risk("2026-09-30", __import__("datetime").date(2026, 9, 23), 3) is False
    assert skonto.skonto_at_risk(None, __import__("datetime").date(2026, 9, 23), 3) is False


# --- duplicates -----------------------------------------------------------------

def test_exact_duplicate_detected(cfg, supplier):
    inv = _invoice(invnum="RE-2026-9999")
    posted = [PostedInvoice(supplier_id="S01", invoice_number="RE-2026-9999",
                             invoice_date="2026-08-01", gross_eur=Decimal("50.00"), paid_date="2026-08-10")]
    findings = match.rule_duplicate_exact(inv, supplier, posted, cfg)
    assert len(findings) == 1
    assert findings[0].rule_id == "DUPLICATE_EXACT"
    assert findings[0].severity == "critical"
    assert findings[0].amount_impact_eur == Decimal("50.00")


def test_exact_duplicate_silent_for_different_invoice_number(cfg, supplier):
    posted = [PostedInvoice(supplier_id="S01", invoice_number="RE-OTHER",
                             invoice_date="2026-08-01", gross_eur=Decimal("50.00"), paid_date="2026-08-10")]
    assert match.rule_duplicate_exact(_invoice(invnum="RE-9"), supplier, posted, cfg) == []


def test_fuzzy_duplicate_detected_within_window(cfg, supplier):
    inv = _invoice(invnum="RE-NEW", date="2026-09-15", gross="120.00")
    posted = [PostedInvoice(supplier_id="S01", invoice_number="RE-OLD",
                             invoice_date="2026-09-01", gross_eur=Decimal("120.00"), paid_date="2026-09-05")]
    findings = match.rule_duplicate_fuzzy(inv, supplier, posted, cfg)
    assert len(findings) == 1
    assert findings[0].rule_id == "DUPLICATE_FUZZY"
    assert findings[0].severity == "warning"


def test_fuzzy_duplicate_silent_outside_window(cfg, supplier):
    inv = _invoice(invnum="RE-NEW", date="2026-09-15", gross="120.00")
    posted = [PostedInvoice(supplier_id="S01", invoice_number="RE-OLD",
                             invoice_date="2026-01-01", gross_eur=Decimal("120.00"), paid_date="2026-01-05")]
    assert match.rule_duplicate_fuzzy(inv, supplier, posted, cfg) == []


def test_fuzzy_duplicate_silent_different_amount(cfg, supplier):
    inv = _invoice(invnum="RE-NEW", date="2026-09-15", gross="120.00")
    posted = [PostedInvoice(supplier_id="S01", invoice_number="RE-OLD",
                             invoice_date="2026-09-01", gross_eur=Decimal("999.00"), paid_date="2026-09-05")]
    assert match.rule_duplicate_fuzzy(inv, supplier, posted, cfg) == []


# --- IBAN mismatch --------------------------------------------------------------

def test_iban_mismatch_detected(cfg, supplier):
    inv = _invoice(iban="DE99999999999999999999")
    findings = match.rule_iban_mismatch(inv, supplier, cfg)
    assert len(findings) == 1
    assert findings[0].rule_id == "IBAN_MISMATCH"
    assert findings[0].severity == "critical"


def test_iban_match_silent(cfg, supplier):
    assert match.rule_iban_mismatch(_invoice(), supplier, cfg) == []


# --- decision precedence ---------------------------------------------------------

def test_decide_precedence_critical_wins():
    findings = [
        Finding("A", "warning", "m", "r", "a"),
        Finding("B", "critical", "m", "r", "a"),
        Finding("C", "warning", "m", "r", "a", hold=True),
    ]
    assert match.decide(findings) == "BLOCKED"


def test_decide_precedence_hold_over_warning():
    findings = [Finding("A", "warning", "m", "r", "a"), Finding("B", "warning", "m", "r", "a", hold=True)]
    assert match.decide(findings) == "HOLD"


def test_decide_precedence_warning_over_info():
    findings = [Finding("A", "info", "m", "-", "a"), Finding("B", "warning", "m", "r", "a")]
    assert match.decide(findings) == "EXCEPTION"


def test_decide_approved_when_no_findings():
    assert match.decide([]) == "APPROVED"


def test_decide_approved_with_only_info_finding():
    assert match.decide([Finding("A", "info", "m", "-", "a")]) == "APPROVED"
