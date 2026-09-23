"""Every sample invoice must extract to the expected header totals + line
count, regardless of which of the 3 free-text layouts (or the XML
e-invoice path) it uses. expected_invoices.json was generated alongside
the sample data itself, so it is the ground truth the demo script promises.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from apmatch import einvoice, extract

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EXPECTED = json.loads((Path(__file__).resolve().parent / "expected_invoices.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("expected", EXPECTED, ids=lambda e: e["file"])
def test_invoice_matches_expected(expected):
    path = DATA_DIR / "invoices" / expected["file"]
    if path.suffix.lower() == ".xml":
        invoice = einvoice.parse_einvoice(path)
    else:
        invoice = extract.extract_heuristic(path.read_text(encoding="utf-8"), path.name)

    assert invoice.invoice_number == expected["invoice_number"]
    assert invoice.invoice_date == expected["invoice_date"]
    assert invoice.po_number == expected["po_number"]
    assert invoice.iban == expected["iban"]
    assert invoice.net_eur == Decimal(expected["net"])
    assert invoice.vat_amount_eur == Decimal(expected["vat_amount"])
    assert invoice.gross_eur == Decimal(expected["gross"])
    assert int(invoice.vat_rate_pct) == expected["vat_pct"]
    assert len(invoice.lines) == expected["n_lines"]
    assert invoice.extraction_source == expected["source"]


def test_all_line_totals_consistent_with_qty_times_price():
    """Independent of the fixture: every extracted line's total should
    equal qty * unit_price (proves the extractor read real numbers, not
    something garbled)."""
    for expected in EXPECTED:
        path = DATA_DIR / "invoices" / expected["file"]
        if path.suffix.lower() == ".xml":
            invoice = einvoice.parse_einvoice(path)
        else:
            invoice = extract.extract_heuristic(path.read_text(encoding="utf-8"), path.name)
        for line in invoice.lines:
            assert line.line_total_eur == (line.qty * line.unit_price_eur).quantize(Decimal("0.01")) \
                or abs(line.line_total_eur - line.qty * line.unit_price_eur) < Decimal("0.02")


def test_german_number_parsing():
    assert extract.parse_de_number("1.234,56") == Decimal("1234.56")
    assert extract.parse_de_number("4,20") == Decimal("4.20")
    assert extract.parse_de_number("97,50") == Decimal("97.50")
    assert extract.parse_de_number("15") == Decimal("15")


def test_german_date_parsing():
    assert extract.parse_de_date("23.09.2026") == "2026-09-23"
    assert extract.parse_de_date("05.01.2026") == "2026-01-05"


def test_extract_heuristic_missing_field_raises():
    with pytest.raises(ValueError):
        extract.extract_heuristic("This is not an invoice at all.", "bogus.txt")
