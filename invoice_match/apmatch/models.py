"""Core dataclasses shared across the pipeline.

Money is always `Decimal`, rounded to cents with ROUND_HALF_UP via
`round_eur` below - never `float`. A finance-adjacent demo that used float
math for money would be the first thing an interviewer pokes at.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def round_eur(value: Decimal) -> Decimal:
    """Round a Decimal EUR amount to whole cents, ROUND_HALF_UP (the
    convention German accounting uses - "kaufmännisches Runden")."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Supplier:
    supplier_id: str
    name: str
    vat_id: str
    iban: str
    skonto_pct: Decimal
    skonto_days: int
    net_days: int


@dataclass
class POLine:
    po_number: str
    supplier_id: str
    store: str
    line: int
    sku: str
    description: str
    qty_ordered: Decimal
    unit_price_eur: Decimal
    order_date: str  # ISO yyyy-mm-dd


@dataclass
class GoodsReceipt:
    gr_id: str
    po_number: str
    sku: str
    qty_received: Decimal
    received_date: str  # ISO
    store: str


@dataclass
class PostedInvoice:
    """One row of payment history (data/posted_invoices.csv), used to catch
    duplicate resubmissions of invoices that were already paid."""
    supplier_id: str
    invoice_number: str
    invoice_date: str  # ISO
    gross_eur: Decimal
    paid_date: str  # ISO


@dataclass
class InvoiceLine:
    sku: str
    description: str
    qty: Decimal
    unit_price_eur: Decimal
    line_total_eur: Decimal


@dataclass
class Invoice:
    """An invoice as parsed - either straight from a structured e-invoice
    XML, or extracted from free text by extract.py (heuristic or LLM)."""
    source_file: str
    supplier_vat_id: str
    invoice_number: str
    invoice_date: str  # ISO yyyy-mm-dd
    po_number: str | None
    iban: str
    net_eur: Decimal
    vat_rate_pct: Decimal
    vat_amount_eur: Decimal
    gross_eur: Decimal
    lines: list[InvoiceLine] = field(default_factory=list)
    store: str | None = None
    extraction_source: str = "heuristic"  # "e-invoice" | "heuristic" | "llm"
    extraction_note: str = ""

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "supplier_vat_id": self.supplier_vat_id,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date,
            "po_number": self.po_number,
            "iban": self.iban,
            "net_eur": str(self.net_eur),
            "vat_rate_pct": str(self.vat_rate_pct),
            "vat_amount_eur": str(self.vat_amount_eur),
            "gross_eur": str(self.gross_eur),
            "lines": [
                {
                    "sku": l.sku, "description": l.description, "qty": str(l.qty),
                    "unit_price_eur": str(l.unit_price_eur), "line_total_eur": str(l.line_total_eur),
                }
                for l in self.lines
            ],
            "store": self.store,
            "extraction_source": self.extraction_source,
            "extraction_note": self.extraction_note,
        }


# Severities a Finding can carry. "hold" is an extra pseudo-severity beyond
# the spec's info/warning/critical triple: it marks findings that should
# route the invoice to automatic re-queue rather than a human queue (see
# Finding.hold below and the decision precedence in match.decide()).
SEVERITIES = ("info", "warning", "critical")


def _json_safe(value):
    """Recursively convert Decimal -> str inside a details dict/list so it
    survives json.dumps unchanged in meaning (used by Finding.to_dict)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@dataclass
class Finding:
    rule_id: str
    severity: str  # "info" | "warning" | "critical"
    message: str
    route_to: str
    suggested_action: str
    amount_impact_eur: Decimal = Decimal("0.00")
    # True for findings that mean "not wrong, just not postable yet - try
    # again automatically" (currently only GR_NOT_POSTED). Kept separate
    # from `severity` so severity stays exactly the three values the report
    # displays, while decide() can still give HOLD its own precedence tier.
    hold: bool = False
    # Structured data behind `message` (e.g. per-line SKU/qty/price for a
    # price deviation), keyed per rule_id - see match.py's rule functions
    # for what each rule_id populates. `message` is the English, report-
    # facing sentence; `details` is what drafts.py uses to build a fully
    # German supplier letter without ever interpolating `message` into it.
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "route_to": self.route_to,
            "suggested_action": self.suggested_action,
            "amount_impact_eur": str(self.amount_impact_eur),
            "hold": self.hold,
            "details": _json_safe(self.details),
        }


@dataclass
class MatchResult:
    invoice: Invoice
    supplier: Supplier | None
    findings: list[Finding]
    decision: str  # "APPROVED" | "EXCEPTION" | "HOLD" | "BLOCKED"
    route_to: str
    payable_eur: Decimal
    skonto_eur: Decimal
    skonto_deadline: str | None  # ISO date or None if not skonto-eligible
    pay_by: str | None  # ISO date AP should actually pay by
    processing_path: str  # "auto" | "manual"

    def to_dict(self) -> dict:
        return {
            "invoice": self.invoice.to_dict(),
            "supplier": (
                {
                    "supplier_id": self.supplier.supplier_id, "name": self.supplier.name,
                    "vat_id": self.supplier.vat_id, "iban": self.supplier.iban,
                    "skonto_pct": str(self.supplier.skonto_pct),
                    "skonto_days": self.supplier.skonto_days, "net_days": self.supplier.net_days,
                }
                if self.supplier else None
            ),
            "findings": [f.to_dict() for f in self.findings],
            "decision": self.decision,
            "route_to": self.route_to,
            "payable_eur": str(self.payable_eur),
            "skonto_eur": str(self.skonto_eur),
            "skonto_deadline": self.skonto_deadline,
            "pay_by": self.pay_by,
            "processing_path": self.processing_path,
        }
