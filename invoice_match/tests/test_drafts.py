"""drafts.py: which rule_ids get a drafted letter and which don't."""
from decimal import Decimal

from apmatch.drafts import draft_for
from apmatch.models import Finding, Invoice, InvoiceLine, MatchResult, Supplier


def _result(findings, decision="EXCEPTION"):
    inv = Invoice(source_file="t.txt", supplier_vat_id="DE1", invoice_number="RE-1", invoice_date="2026-09-15",
                  po_number="PO-1", iban="DE00", net_eur=Decimal("100"), vat_rate_pct=Decimal("19"),
                  vat_amount_eur=Decimal("19"), gross_eur=Decimal("119"),
                  lines=[InvoiceLine("X", "x", Decimal(1), Decimal("100"), Decimal("100"))])
    supplier = Supplier("S01", "Test GmbH", "DE1", "DE00", Decimal("2.0"), 10, 30)
    return MatchResult(invoice=inv, supplier=supplier, findings=findings, decision=decision,
                        route_to="-", payable_eur=Decimal("100.00"), skonto_eur=Decimal("0.00"),
                        skonto_deadline=None, pay_by=None, processing_path="manual")


def test_price_deviation_gets_a_draft():
    f = Finding("PRICE_DEVIATION", "warning", "price off", "Einkauf", "credit note",
                details={"lines": [{"sku": "FA-DISP", "qty": Decimal("25"),
                                     "invoiced_price": Decimal("22.26"), "po_price": Decimal("21.00"),
                                     "deviation_pct": Decimal("6.0")}]})
    text = draft_for(_result([f]))
    assert text is not None
    assert "Gutschrift" in text
    assert "RE-1" in text
    # German amounts, matching the coordinator's example wording exactly.
    assert "Art.-Nr. FA-DISP: berechnet 22,26 EUR/Stück, vereinbart laut Bestellung 21,00 EUR/Stück" in text
    # No leftover English finding text ("vs PO", "within tolerance", ...).
    assert "vs PO" not in text
    assert "price off" not in text


def test_short_delivery_draft_uses_german_detail_lines():
    f = Finding("QTY_SHORT", "warning", "invoiced 40, received 32 (short 8)", "Filiale", "credit note",
                details={"lines": [{"sku": "WK-HANDSCH", "invoiced_qty": Decimal("40"),
                                     "received_qty": Decimal("32"), "shortfall": Decimal("8"),
                                     "unit_price": Decimal("6.90")}]})
    text = draft_for(_result([f]))
    assert text is not None
    assert "Art.-Nr. WK-HANDSCH: berechnet 40 Stück, geliefert 32 Stück (Differenz 8)" in text
    assert "invoiced 40" not in text


def test_iban_mismatch_gets_no_draft():
    f = Finding("IBAN_MISMATCH", "critical", "iban off", "Finance Lead", "call")
    assert draft_for(_result([f], decision="BLOCKED")) is None


def test_gr_not_posted_gets_no_draft():
    f = Finding("GR_NOT_POSTED", "warning", "no gr", "Wiedervorlage", "wait", hold=True)
    assert draft_for(_result([f], decision="HOLD")) is None


def test_no_findings_gets_no_draft():
    assert draft_for(_result([], decision="APPROVED")) is None


def test_duplicate_exact_draft_mentions_already_paid():
    f = Finding("DUPLICATE_EXACT", "critical", "already paid on 2026-08-30", "AP", "do not pay",
                details={"paid_date": "2026-08-30", "gross_eur": Decimal("119.00")})
    text = draft_for(_result([f], decision="BLOCKED"))
    assert text is not None
    assert "bereits beglichen" in text
    assert "30.08.2026" in text  # German date format, not ISO
    assert "119,00 EUR" in text
