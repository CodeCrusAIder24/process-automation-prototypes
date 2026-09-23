"""out/report.html: the extraction-mode copy in the pipeline band must
depend on the run's actual extraction mode (critique P1 - piece #1's
report once claimed "LLM" while running heuristic; this pins the fix for
invoice_match's own report), and the page must carry every German section
id the locked design specifies."""
import tomllib
from datetime import date
from pathlib import Path

import pytest

from apmatch import einvoice, extract, ingest, match, report
from apmatch import kpi as kpi_mod

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TODAY = date(2026, 9, 23)

GERMAN_SECTION_IDS = ["nutzen", "gestoppt", "arbeitsliste", "funktionsweise", "rechnungen", "annahmen"]


@pytest.fixture(scope="module")
def results():
    with open(ROOT / "rules.toml", "rb") as f:
        cfg = tomllib.load(f)
    suppliers = ingest.load_suppliers(DATA_DIR / "suppliers.csv")
    pos = ingest.load_purchase_orders(DATA_DIR / "purchase_orders.csv")
    grs = ingest.load_goods_receipts(DATA_DIR / "goods_receipts.csv")
    posted = ingest.load_posted_invoices(DATA_DIR / "posted_invoices.csv")

    out = []
    for path in ingest.list_invoice_files(DATA_DIR / "invoices"):
        if path.suffix.lower() == ".xml":
            invoice = einvoice.parse_einvoice(path)
        else:
            invoice = extract.extract_heuristic(path.read_text(encoding="utf-8"), path.name)
        out.append(match.match_invoice(invoice, suppliers, pos, grs, posted, cfg, TODAY))
    return out, cfg


def _build_report(results, cfg, extraction_mode, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    batch = kpi_mod.batch_kpis(results)
    extrap = kpi_mod.extrapolate(batch, cfg["kpi"])
    skonto_year = kpi_mod.skonto_recovered_per_year(results, cfg["kpi"])
    path = report.write_report_html(results, batch, extrap, skonto_year, cfg, "2026-09-23", extraction_mode, out_dir)
    return path.read_text(encoding="utf-8")


def test_report_contains_german_section_ids(results, tmp_path):
    res, cfg = results
    html_text = _build_report(res, cfg, "heuristic", tmp_path)
    for section_id in GERMAN_SECTION_IDS:
        assert f'id="{section_id}"' in html_text, f"missing section id={section_id!r}"


def test_extraction_mode_copy_differs_heuristic_vs_llm(results, tmp_path):
    res, cfg = results
    heuristic_html = _build_report(res, cfg, "heuristic", tmp_path / "heuristic")
    llm_html = _build_report(res, cfg, "llm", tmp_path / "llm")

    # The pipeline-band copy for step 1.0 must reflect what THIS run
    # actually did (critique P1), not a fixed claim - so the two runs'
    # HTML must differ, and each must state what IT did (the heuristic
    # copy also mentions LLM extraction as a future option, so the
    # assertions use each mode's distinctive "this run did X" phrasing,
    # not a bare substring both variants share).
    assert heuristic_html != llm_html
    assert "Dieser Lauf nutzte die regelbasierte Heuristik" in heuristic_html
    assert "Dieser Lauf nutzte die regelbasierte Heuristik" not in llm_html
    assert "dieser Lauf nutzte LLM-Extraktion" in llm_html
    assert "dieser Lauf nutzte LLM-Extraktion" not in heuristic_html

    # Nav meta badge also reflects the run's mode.
    assert "Extraktion: Heuristik" in heuristic_html
    assert "Extraktion: LLM" in llm_html


def test_hero_headline_reduction_is_computed_not_hardcoded(results, tmp_path):
    """-72% in the hero figure must equal round((before-after)/before*100)
    from kpi.extrapolate's own numbers, not a literal constant."""
    res, cfg = results
    batch = kpi_mod.batch_kpis(res)
    extrap = kpi_mod.extrapolate(batch, cfg["kpi"])
    before, after = float(extrap["hours_before_per_month"]), float(extrap["hours_after_per_month"])
    expected_pct = round((before - after) / before * 100)

    html_text = _build_report(res, cfg, "heuristic", tmp_path)
    assert f'data-count="{expected_pct}"' in html_text


def test_no_english_finding_message_leaks_into_report(results, tmp_path):
    """The report's reason/action text is built from _de_reason/_de_action
    (structured `details`), never from Finding.message, which stays
    English for console/worklist.csv/results.json."""
    res, cfg = results
    html_text = _build_report(res, cfg, "heuristic", tmp_path)
    # A few distinctive English fragments that only ever appear in
    # Finding.message / suggested_action, never in the German copy.
    for fragment in ("Unit price above PO price", "Request a credit note",
                      "Short delivery (Mindermenge): WK-HANDSCH: invoiced"):
        assert fragment not in html_text
