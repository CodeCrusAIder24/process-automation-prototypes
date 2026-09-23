"""Dedicated tests for the simplified-UBL e-invoice parser (einvoice.py).
test_extract.py already checks the header totals via the shared fixture;
this file checks the XML/namespace-specific details."""
from decimal import Decimal
from pathlib import Path

from apmatch import einvoice

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "invoices"


def test_parse_einvoice_extraction_source():
    invoice = einvoice.parse_einvoice(DATA_DIR / "inv-03-s03.xml")
    assert invoice.extraction_source == "e-invoice"
    assert invoice.extraction_note  # explains why extraction was skipped


def test_parse_einvoice_lines_and_sku():
    invoice = einvoice.parse_einvoice(DATA_DIR / "inv-03-s03.xml")
    skus = {l.sku for l in invoice.lines}
    assert skus == {"HP-DIELE", "HP-PFOSTEN"}
    diele = next(l for l in invoice.lines if l.sku == "HP-DIELE")
    assert diele.qty == Decimal("60")
    assert diele.unit_price_eur == Decimal("18.50")
    assert diele.line_total_eur == Decimal("1110.00")


def test_parse_einvoice_store_from_buyer_party_name():
    invoice = einvoice.parse_einvoice(DATA_DIR / "inv-03-s03.xml")
    assert invoice.store == "LU1"


def test_parse_einvoice_vat_rate():
    plants = einvoice.parse_einvoice(DATA_DIR / "inv-10-s06.xml")
    assert plants.vat_rate_pct == Decimal("7.00")
    tools = einvoice.parse_einvoice(DATA_DIR / "inv-07-s01.xml")
    assert tools.vat_rate_pct == Decimal("19.00")
