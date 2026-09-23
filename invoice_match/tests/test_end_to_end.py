"""Run the full pipeline (ingest -> extract -> match) on data/ and check
every one of the 18 sample invoices lands on exactly the decision the demo
script promises."""
import tomllib
from datetime import date
from pathlib import Path

import pytest

from apmatch import einvoice, extract, ingest, match

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TODAY = date(2026, 9, 23)

EXPECTED_DECISIONS = {
    "inv-01-s01.txt": "APPROVED", "inv-02-s02.txt": "APPROVED", "inv-03-s03.xml": "APPROVED",
    "inv-04-s04.txt": "APPROVED", "inv-05-s05.txt": "APPROVED", "inv-06-s06.txt": "APPROVED",
    "inv-07-s01.xml": "APPROVED", "inv-08-s02.txt": "APPROVED", "inv-09-s03.txt": "APPROVED",
    "inv-10-s06.xml": "APPROVED", "inv-11-s01.txt": "APPROVED",
    "inv-12-s05.txt": "EXCEPTION", "inv-13-s02.txt": "EXCEPTION",
    "inv-14-s01.txt": "HOLD",
    "inv-15-s03.txt": "BLOCKED", "inv-16-s04.txt": "BLOCKED",
    "inv-17-s02.txt": "EXCEPTION", "inv-18-s06.txt": "EXCEPTION",
}


@pytest.fixture(scope="module")
def results():
    with open(ROOT / "rules.toml", "rb") as f:
        cfg = tomllib.load(f)
    suppliers = ingest.load_suppliers(DATA_DIR / "suppliers.csv")
    pos = ingest.load_purchase_orders(DATA_DIR / "purchase_orders.csv")
    grs = ingest.load_goods_receipts(DATA_DIR / "goods_receipts.csv")
    posted = ingest.load_posted_invoices(DATA_DIR / "posted_invoices.csv")

    out = {}
    for path in ingest.list_invoice_files(DATA_DIR / "invoices"):
        if path.suffix.lower() == ".xml":
            invoice = einvoice.parse_einvoice(path)
        else:
            invoice = extract.extract_heuristic(path.read_text(encoding="utf-8"), path.name)
        out[path.name] = match.match_invoice(invoice, suppliers, pos, grs, posted, cfg, TODAY)
    return out


def test_all_18_sample_invoices_present(results):
    assert set(results.keys()) == set(EXPECTED_DECISIONS.keys())


@pytest.mark.parametrize("filename,expected_decision", sorted(EXPECTED_DECISIONS.items()))
def test_decision_matches_demo_script(results, filename, expected_decision):
    assert results[filename].decision == expected_decision, (
        f"{filename}: expected {expected_decision}, got {results[filename].decision} "
        f"(findings: {[f.rule_id for f in results[filename].findings]})"
    )


def test_invoice_16_is_blocked_by_iban_not_by_anything_else(results):
    """Invoice 16 matches PO/GR perfectly - IBAN mismatch alone must be
    what blocks it, proving the rule fires independently of the 3-way match."""
    r = results["inv-16-s04.txt"]
    rule_ids = {f.rule_id for f in r.findings}
    assert rule_ids == {"IBAN_MISMATCH"}
    assert r.route_to == "Finance Lead (Vier-Augen-Prinzip)"


def test_invoice_15_duplicate_route_and_zero_payable(results):
    r = results["inv-15-s03.txt"]
    assert r.payable_eur == 0
    assert r.route_to == "Kreditorenbuchhaltung"


def test_invoice_14_hold_is_skonto_at_risk(results):
    from apmatch import skonto
    r = results["inv-14-s01.txt"]
    assert r.decision == "HOLD"
    assert skonto.skonto_at_risk(r.skonto_deadline, TODAY, 3) is True


def test_touchless_rate_matches_batch(results):
    approved = sum(1 for r in results.values() if r.decision == "APPROVED")
    assert approved == 11
