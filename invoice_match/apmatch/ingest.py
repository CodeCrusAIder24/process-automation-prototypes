"""Load the CSV master data (suppliers, purchase orders, goods receipts,
payment history) and list the incoming invoice files to process."""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .models import GoodsReceipt, POLine, PostedInvoice, Supplier


def load_suppliers(path: Path) -> dict[str, Supplier]:
    """Returns {supplier_id: Supplier} and also indexes are built by the
    caller when needed by VAT ID (see match.find_supplier_by_vat)."""
    suppliers: dict[str, Supplier] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            suppliers[row["supplier_id"]] = Supplier(
                supplier_id=row["supplier_id"],
                name=row["name"],
                vat_id=row["vat_id"],
                iban=row["iban"],
                skonto_pct=Decimal(row["skonto_pct"]),
                skonto_days=int(row["skonto_days"]),
                net_days=int(row["net_days"]),
            )
    return suppliers


def load_purchase_orders(path: Path) -> dict[str, list[POLine]]:
    """Returns {po_number: [POLine, ...]}, lines in file order."""
    pos: dict[str, list[POLine]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            line = POLine(
                po_number=row["po_number"],
                supplier_id=row["supplier_id"],
                store=row["store"],
                line=int(row["line"]),
                sku=row["sku"],
                description=row["description"],
                qty_ordered=Decimal(row["qty_ordered"]),
                unit_price_eur=Decimal(row["unit_price_eur"]),
                order_date=row["order_date"],
            )
            pos.setdefault(line.po_number, []).append(line)
    return pos


def load_goods_receipts(path: Path) -> dict[str, list[GoodsReceipt]]:
    """Returns {po_number: [GoodsReceipt, ...]}. A PO with no key present
    (or an empty list) means "nothing received yet" - see match.rule_gr_missing."""
    grs: dict[str, list[GoodsReceipt]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            gr = GoodsReceipt(
                gr_id=row["gr_id"],
                po_number=row["po_number"],
                sku=row["sku"],
                qty_received=Decimal(row["qty_received"]),
                received_date=row["received_date"],
                store=row["store"],
            )
            grs.setdefault(gr.po_number, []).append(gr)
    return grs


def load_posted_invoices(path: Path) -> list[PostedInvoice]:
    """Payment history: invoices already checked in and paid in the past.
    Used only to detect duplicate resubmissions, not for matching."""
    out: list[PostedInvoice] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(PostedInvoice(
                supplier_id=row["supplier_id"],
                invoice_number=row["invoice_number"],
                invoice_date=row["invoice_date"],
                gross_eur=Decimal(row["gross_eur"]),
                paid_date=row["paid_date"],
            ))
    return out


def list_invoice_files(invoices_dir: Path) -> list[Path]:
    """All incoming invoices (.txt free text + .xml e-invoices), sorted by
    filename so the batch order (and hence the demo console table) is
    stable and reproducible across runs and operating systems."""
    files = [p for p in invoices_dir.iterdir() if p.suffix.lower() in (".txt", ".xml")]
    return sorted(files, key=lambda p: p.name)
