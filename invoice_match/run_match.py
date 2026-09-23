#!/usr/bin/env python3
"""CLI entry point for the invoice-matching pipeline.

Usage (run from inside invoice_match/, or as `python invoice_match/run_match.py`
from the repo root - paths are always resolved relative to this script, not
the current working directory):

    python run_match.py [--llm] [--today YYYY-MM-DD] [--open]

--llm     use the LLM extractor for free-text invoices instead of the
          regex heuristic (structured e-invoice XML always skips both).
--today   "today" for skonto-deadline calculations. Defaults to
          2026-09-23 so the demo is reproducible.
--open    open out/report.html in the default browser when done.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from apmatch import einvoice, extract, ingest, match, report  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="3-way match supplier invoices against PO + goods receipt.")
    p.add_argument("--llm", action="store_true", help="Use the LLM extractor for free-text invoices.")
    p.add_argument("--today", type=str, default="2026-09-23", help="Reference date (YYYY-MM-DD) for skonto deadlines.")
    p.add_argument("--open", action="store_true", help="Open out/report.html in the default browser when done.")
    return p.parse_args(argv)


def _parse_iso_date(value: str) -> date:
    y, m, d = value.split("-")
    return date(int(y), int(m), int(d))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    today = _parse_iso_date(args.today)

    data_dir = SCRIPT_DIR / "data"
    out_dir = SCRIPT_DIR / "out"
    rules_path = SCRIPT_DIR / "rules.toml"

    import tomllib
    with open(rules_path, "rb") as f:
        rules_cfg = tomllib.load(f)

    suppliers = ingest.load_suppliers(data_dir / "suppliers.csv")
    purchase_orders = ingest.load_purchase_orders(data_dir / "purchase_orders.csv")
    goods_receipts = ingest.load_goods_receipts(data_dir / "goods_receipts.csv")
    posted_invoices = ingest.load_posted_invoices(data_dir / "posted_invoices.csv")
    invoice_files = ingest.list_invoice_files(data_dir / "invoices")

    llm_config = None
    if args.llm:
        extract.load_dotenv_files(SCRIPT_DIR)
        try:
            llm_config = extract.resolve_llm_config()
        except extract.LLMConfigError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    results = []
    for path in invoice_files:
        if path.suffix.lower() == ".xml":
            invoice = einvoice.parse_einvoice(path)
        else:
            text = path.read_text(encoding="utf-8")
            if llm_config is not None:
                base_url, api_key, model = llm_config
                invoice = extract.extract_llm(text, path.name, base_url, api_key, model)
            else:
                invoice = extract.extract_heuristic(text, path.name)
        results.append(match.match_invoice(
            invoice, suppliers, purchase_orders, goods_receipts, posted_invoices, rules_cfg, today,
        ))

    extraction_mode = "llm" if args.llm else "heuristic"
    report.print_console_table(results, args.today, extraction_mode, rules_path)

    paths, batch, extrap, skonto_year = report.write_all(results, rules_cfg, args.today, extraction_mode, out_dir)
    print()
    report.print_kpi_block(batch, extrap, skonto_year)
    print()
    print("Output files:")
    for label, path in paths.items():
        print(f"  {label}: {path}")
    print(f"  drafts: {out_dir / 'drafts'}")

    if args.open:
        webbrowser.open(paths["report_html"].resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
