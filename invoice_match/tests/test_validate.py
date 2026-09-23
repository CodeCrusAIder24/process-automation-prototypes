"""validate.py: arithmetic plausibility checks."""
from decimal import Decimal

from apmatch.models import Invoice, InvoiceLine
from apmatch.validate import validate_invoice


def _invoice(net, vat_rate, vat_amount, gross, lines):
    return Invoice(
        source_file="t.txt", supplier_vat_id="DE111111111", invoice_number="RE-1",
        invoice_date="2026-09-15", po_number="PO-1", iban="DE00000000000000000000",
        net_eur=Decimal(net), vat_rate_pct=Decimal(vat_rate), vat_amount_eur=Decimal(vat_amount),
        gross_eur=Decimal(gross), lines=lines,
    )


def _line(qty, price):
    total = Decimal(qty) * Decimal(price)
    return InvoiceLine(sku="X", description="x", qty=Decimal(qty), unit_price_eur=Decimal(price),
                        line_total_eur=total)


def test_reconciling_invoice_has_no_finding():
    inv = _invoice("100.00", "19", "19.00", "119.00", [_line(10, "10.00")])
    assert validate_invoice(inv, Decimal("5.00"), "AP") == []


def test_small_rounding_within_tolerance_has_no_finding():
    inv = _invoice("100.02", "19", "19.00", "119.02", [_line(10, "10.00")])
    assert validate_invoice(inv, Decimal("5.00"), "AP") == []


def test_line_sum_vs_net_mismatch_detected():
    """Sample invoice 18: line totals sum to 372.00 but stated Nettobetrag
    is 382.00 (off by 10.00, above the 5.00 tolerance)."""
    lines = [_line(40, "4.60"), _line(20, "9.40")]  # sums to 372.00
    inv = _invoice("382.00", "7", "26.74", "408.74", lines)
    findings = validate_invoice(inv, Decimal("5.00"), "Kreditorenbuchhaltung")
    assert len(findings) == 1
    assert findings[0].rule_id == "ARITHMETIC_MISMATCH"
    assert findings[0].severity == "warning"
    assert findings[0].amount_impact_eur == Decimal("10.00")


def test_net_times_vat_vs_gross_mismatch_detected():
    inv = _invoice("100.00", "19", "19.00", "150.00", [_line(10, "10.00")])
    findings = validate_invoice(inv, Decimal("5.00"), "AP")
    assert len(findings) == 1
    assert findings[0].rule_id == "ARITHMETIC_MISMATCH"
    assert findings[0].amount_impact_eur == Decimal("31.00")
