"""The rules engine: 3-way match (invoice vs. purchase order vs. goods
receipt), duplicate detection, bank-detail check, and the decision.

Every rule below is a small pure function: (data in) -> list[Finding]. None
of them call an LLM or make a network call - this is the deterministic,
auditable half of the pipeline that the AI-extracted invoice data is
checked against (see apmatch/__init__.py for why that split matters).

Decision precedence (any finding of that severity anywhere wins):
    any critical finding        -> BLOCKED
    else any hold-type finding  -> HOLD   (not an exception - automatic re-check)
    else any warning finding    -> EXCEPTION
    else                        -> APPROVED
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from . import skonto
from .formatting import fmt_de_date, fmt_eur
from .models import Finding, GoodsReceipt, Invoice, MatchResult, POLine, PostedInvoice, Supplier, round_eur
from .validate import validate_invoice

# Money-at-risk rule_ids that feed the headline "money at risk caught" KPI
# (see kpi.py). PO_UNKNOWN/SUPPLIER_UNKNOWN/GR_NOT_POSTED are real work
# items but are process gaps, not a quantified EUR amount at risk in the
# same sense, so they are deliberately excluded from that sum.
MONEY_AT_RISK_RULES = {
    "PRICE_DEVIATION", "QTY_SHORT", "DUPLICATE_EXACT", "DUPLICATE_FUZZY",
    "IBAN_MISMATCH", "ARITHMETIC_MISMATCH",
}

# Human-readable label per rule_id, English with the German term in
# parentheses (same convention the spec asks for in report copy). Used by
# report.py for the money-at-risk breakdown and the "how decisions are
# made" rules table, so a recruiter never has to decode a bare rule_id.
# "DUPLICATE" is the merged label for the money-at-risk bucket that
# combines DUPLICATE_EXACT + DUPLICATE_FUZZY (see kpi.money_at_risk_by_rule).
RULE_LABELS = {
    "SUPPLIER_UNKNOWN": "Unknown supplier (Lieferant unbekannt)",
    "PO_UNKNOWN": "Missing purchase order (Bestellung fehlt)",
    "PRICE_DEVIATION": "Price deviation (Preisabweichung)",
    "PRICE_WITHIN_TOLERANCE": "Price within tolerance (Preistoleranz eingehalten)",
    "QTY_SHORT": "Short delivery (Mindermenge)",
    "GR_NOT_POSTED": "Goods receipt missing (Wareneingang fehlt)",
    "DUPLICATE_EXACT": "Duplicate invoice (Doppelrechnung)",
    "DUPLICATE_FUZZY": "Possible duplicate invoice (Doppelrechnung, Verdacht)",
    "DUPLICATE": "Duplicate invoice (Doppelrechnung)",
    "IBAN_MISMATCH": "Changed bank details (IBAN-Abweichung)",
    "ARITHMETIC_MISMATCH": "Invoice arithmetic error (Rechenfehler)",
}


def rule_label(rule_id: str) -> str:
    return RULE_LABELS.get(rule_id, rule_id)


def _route(cfg: dict, rule_id: str) -> str:
    return cfg.get("routing", {}).get(rule_id, "Kreditorenbuchhaltung")


def find_supplier_by_vat(vat_id: str, suppliers: dict[str, Supplier]) -> Supplier | None:
    vat_norm = (vat_id or "").replace(" ", "").upper()
    for s in suppliers.values():
        if s.vat_id.replace(" ", "").upper() == vat_norm:
            return s
    return None


# --- individual rules -------------------------------------------------------

def rule_supplier_unknown(invoice: Invoice, supplier: Supplier | None, cfg: dict) -> list[Finding]:
    """VAT ID on the invoice is not in the supplier master data."""
    if supplier is not None:
        return []
    return [Finding(
        rule_id="SUPPLIER_UNKNOWN", severity="warning",
        message=f"USt-IdNr. {invoice.supplier_vat_id!r} on the invoice is not in the supplier master data.",
        route_to=_route(cfg, "SUPPLIER_UNKNOWN"),
        suggested_action="Verify and create/update the supplier master record before payment.",
        amount_impact_eur=Decimal("0.00"),
        details={"vat_id": invoice.supplier_vat_id},
    )]


def rule_po_unknown(invoice: Invoice, po_lines: list[POLine] | None, cfg: dict) -> list[Finding]:
    """No PO reference on the invoice, or the referenced PO does not exist
    in the ERP (classic "Maverick Buying" - goods ordered outside the
    normal purchasing process, so no PO was ever raised for them)."""
    if invoice.po_number and po_lines is not None:
        return []
    ref = invoice.po_number or "(none given)"
    return [Finding(
        rule_id="PO_UNKNOWN", severity="warning",
        message=f"Purchase order reference {ref!r} is missing or unknown to the ERP.",
        route_to=_route(cfg, "PO_UNKNOWN"),
        suggested_action="Identify the requester/store that ordered without a PO (Maverick Buying) before paying.",
        amount_impact_eur=Decimal("0.00"),
        details={"po_ref": invoice.po_number},
    )]


def rule_price_tolerance(invoice: Invoice, po_lines: list[POLine], cfg: dict) -> list[Finding]:
    """Per-unit price on the invoice vs. the PO, for every invoice line
    that matches a PO line by SKU. Deviations at or below
    [tolerance].price_pct are informational only; above it, the whole
    invoice becomes an EXCEPTION."""
    po_by_sku = {l.sku: l for l in po_lines}
    tol_pct = Decimal(str(cfg["tolerance"]["price_pct"]))

    above_lines, info_lines = [], []
    above_impact = Decimal("0.00")
    for line in invoice.lines:
        po_line = po_by_sku.get(line.sku)
        if po_line is None or po_line.unit_price_eur == 0:
            continue
        dev_pct = abs(line.unit_price_eur - po_line.unit_price_eur) / po_line.unit_price_eur * 100
        detail = {
            "sku": line.sku, "qty": line.qty, "invoiced_price": line.unit_price_eur,
            "po_price": po_line.unit_price_eur, "deviation_pct": dev_pct,
        }
        if dev_pct <= tol_pct:
            if dev_pct > 0:
                info_lines.append(detail)
            continue
        above_lines.append(detail)
        above_impact += round_eur((line.unit_price_eur - po_line.unit_price_eur) * line.qty)

    if above_lines:
        msg_parts = [
            f"{d['sku']}: {fmt_eur(d['invoiced_price'])} vs PO {fmt_eur(d['po_price'])} ({d['deviation_pct']:.2f}%)"
            for d in above_lines
        ]
        return [Finding(
            rule_id="PRICE_DEVIATION", severity="warning",
            message="Unit price above PO price by more than tolerance (" + f"{tol_pct}%): " + "; ".join(msg_parts),
            route_to=_route(cfg, "PRICE_DEVIATION"),
            suggested_action="Request a credit note for the price difference (supplier likely used an outdated price list).",
            amount_impact_eur=above_impact,
            details={"lines": above_lines},
        )]
    if info_lines:
        msg_parts = [
            f"{d['sku']}: {fmt_eur(d['invoiced_price'])} vs PO {fmt_eur(d['po_price'])} "
            f"({d['deviation_pct']:.2f}% - within tolerance)"
            for d in info_lines
        ]
        return [Finding(
            rule_id="PRICE_WITHIN_TOLERANCE", severity="info",
            message="Price deviation within tolerance: " + "; ".join(msg_parts),
            route_to="-", suggested_action="None - informational only.",
            amount_impact_eur=Decimal("0.00"),
            details={"lines": info_lines},
        )]
    return []


def rule_short_delivery(invoice: Invoice, po_lines: list[POLine], gr_lines: list[GoodsReceipt], cfg: dict) -> list[Finding]:
    """Invoiced quantity vs. quantity actually received (Wareneingang) at
    the store, per SKU. Only called once we know a goods receipt exists at
    all for this PO (see rule_gr_missing, checked first)."""
    po_by_sku = {l.sku: l for l in po_lines}
    received_by_sku: dict[str, Decimal] = {}
    for gr in gr_lines:
        received_by_sku[gr.sku] = received_by_sku.get(gr.sku, Decimal(0)) + gr.qty_received

    qty_tol = Decimal(str(cfg["tolerance"]["qty_tolerance"]))
    detail_lines = []
    msg_parts = []
    impact = Decimal("0.00")
    for line in invoice.lines:
        if line.sku not in po_by_sku:
            continue
        received = received_by_sku.get(line.sku, Decimal(0))
        shortfall = line.qty - received
        if shortfall > qty_tol:
            detail_lines.append({
                "sku": line.sku, "invoiced_qty": line.qty, "received_qty": received,
                "shortfall": shortfall, "unit_price": line.unit_price_eur,
            })
            msg_parts.append(f"{line.sku}: invoiced {line.qty}, received {received} (short {shortfall})")
            impact += round_eur(shortfall * line.unit_price_eur)

    if not detail_lines:
        return []
    return [Finding(
        rule_id="QTY_SHORT", severity="warning",
        message="Short delivery (Mindermenge): " + "; ".join(msg_parts),
        route_to=_route(cfg, "QTY_SHORT"),
        suggested_action="Confirm the count with the store and request a credit note for the missing units.",
        amount_impact_eur=impact,
        details={"lines": detail_lines},
    )]


def rule_gr_missing(
    invoice: Invoice, gr_lines: list[GoodsReceipt], supplier: Supplier | None, cfg: dict, today,
) -> list[Finding]:
    """No goods receipt has been posted at all yet for this PO. Not an
    exception (nobody did anything wrong, the delivery is just probably
    still in transit or not yet booked in) - it goes on automatic re-check
    instead of a human queue.

    If the supplier's skonto deadline is close (within [hold].skonto_risk_days),
    the message also spells out the deadline and the EUR discount at risk,
    computed from this invoice's own gross and the supplier's skonto terms -
    never hardcoded - so the urgency is visible in the console/report without
    anyone having to cross-reference the supplier master data by hand."""
    if gr_lines:
        return []
    message = f"No goods receipt has been posted yet for PO {invoice.po_number}."
    details = {"po_number": invoice.po_number}
    if supplier is not None:
        skonto_eur, deadline, _ = skonto.compute_skonto(invoice.gross_eur, supplier, invoice.invoice_date, today)
        risk_days = int(cfg["hold"]["skonto_risk_days"])
        if deadline and skonto_eur > 0 and skonto.skonto_at_risk(deadline, today, risk_days):
            days_left = (skonto.parse_iso(deadline) - today).days
            when = "today" if days_left == 0 else ("in 1 day" if days_left == 1 else f"in {days_left} days")
            message += (
                f" Skonto deadline {fmt_de_date(deadline)} ({when}) - {fmt_eur(skonto_eur)} discount "
                f"at risk if the goods receipt is not posted by then."
            )
            details.update({"skonto_deadline": deadline, "skonto_eur": skonto_eur, "days_left": days_left})
    return [Finding(
        rule_id="GR_NOT_POSTED", severity="warning", hold=True,
        message=message,
        route_to=_route(cfg, "GR_NOT_POSTED"),
        suggested_action="Automatic re-check on next run once the goods receipt is posted (automatische Wiedervorlage).",
        amount_impact_eur=Decimal("0.00"),
        details=details,
    )]


def rule_duplicate_exact(invoice: Invoice, supplier: Supplier | None, posted: list[PostedInvoice], cfg: dict) -> list[Finding]:
    """Same supplier + same invoice number already exists in payment
    history - a resent invoice (Zweitschrift/Zahlungserinnerung) that was
    already paid. Paying it again is a direct cash loss, hence critical."""
    if supplier is None:
        return []
    for p in posted:
        if p.supplier_id == supplier.supplier_id and p.invoice_number == invoice.invoice_number:
            return [Finding(
                rule_id="DUPLICATE_EXACT", severity="critical",
                message=f"Invoice {invoice.invoice_number} was already paid on {p.paid_date} (gross {fmt_eur(p.gross_eur)}).",
                route_to=_route(cfg, "DUPLICATE_EXACT"),
                suggested_action=f"Do not pay. Inform the supplier this invoice was already settled on {p.paid_date}.",
                amount_impact_eur=p.gross_eur,
                details={"paid_date": p.paid_date, "gross_eur": p.gross_eur},
            )]
    return []


def rule_duplicate_fuzzy(invoice: Invoice, supplier: Supplier | None, posted: list[PostedInvoice], cfg: dict) -> list[Finding]:
    """Same supplier, same gross amount, invoice date within
    [duplicates].fuzzy_window_days of an already-paid invoice, but a
    DIFFERENT invoice number - looks like the same delivery invoiced
    twice under two numbers. Softer signal than the exact match, so it is
    a warning (EXCEPTION), not critical."""
    if supplier is None:
        return []
    window_days = int(cfg["duplicates"]["fuzzy_window_days"])
    inv_date = skonto.parse_iso(invoice.invoice_date)
    for p in posted:
        if p.supplier_id != supplier.supplier_id or p.invoice_number == invoice.invoice_number:
            continue
        if p.gross_eur != invoice.gross_eur:
            continue
        delta_days = abs((inv_date - skonto.parse_iso(p.invoice_date)).days)
        if delta_days <= window_days:
            return [Finding(
                rule_id="DUPLICATE_FUZZY", severity="warning",
                message=(f"Same supplier and gross amount ({fmt_eur(invoice.gross_eur)}) as already-paid invoice "
                         f"{p.invoice_number} (paid {p.paid_date}), {delta_days} day(s) apart - possible duplicate."),
                route_to=_route(cfg, "DUPLICATE_FUZZY"),
                suggested_action=f"Verify with the supplier before paying - likely the same delivery resubmitted as {p.invoice_number}.",
                amount_impact_eur=invoice.gross_eur,
                details={
                    "paid_date": p.paid_date, "gross_eur": invoice.gross_eur,
                    "other_invoice_number": p.invoice_number, "days_apart": delta_days,
                },
            )]
    return []


def rule_iban_mismatch(invoice: Invoice, supplier: Supplier | None, cfg: dict) -> list[Finding]:
    """Bank details on the invoice differ from the supplier master data -
    the classic payment-diversion fraud pattern. Everything else about the
    invoice can be perfect; this alone is enough to block payment."""
    if supplier is None:
        return []
    if invoice.iban.replace(" ", "").upper() == supplier.iban.replace(" ", "").upper():
        return []
    return [Finding(
        rule_id="IBAN_MISMATCH", severity="critical",
        message=f"Invoice IBAN {invoice.iban} differs from supplier master data ({supplier.iban}).",
        route_to=_route(cfg, "IBAN_MISMATCH"),
        suggested_action=(
            "Verify the bank-detail change by phone using the number on file in the supplier master "
            "data - never the contact details printed on the invoice itself."
        ),
        amount_impact_eur=invoice.gross_eur,
    )]


# --- decision + payable amount ---------------------------------------------

def decide(findings: list[Finding]) -> str:
    if any(f.severity == "critical" for f in findings):
        return "BLOCKED"
    if any(f.hold for f in findings):
        return "HOLD"
    if any(f.severity == "warning" for f in findings):
        return "EXCEPTION"
    return "APPROVED"


def pick_route(findings: list[Finding], decision: str) -> str:
    if decision == "BLOCKED":
        pool = [f for f in findings if f.severity == "critical"]
    elif decision == "HOLD":
        pool = [f for f in findings if f.hold]
    elif decision == "EXCEPTION":
        pool = [f for f in findings if f.severity == "warning"]
    else:
        return "Automatisch verbucht (kein Review erforderlich)"
    return pool[0].route_to if pool else "Kreditorenbuchhaltung"


def _suggested_payable(
    invoice: Invoice, po_lines: list[POLine] | None, gr_lines: list[GoodsReceipt],
    findings: list[Finding], decision: str,
) -> Decimal:
    """The amount AP should actually expect to pay, given the findings:
    at PO price for a price deviation, at received quantity for a short
    delivery, at the arithmetically-correct total for an arithmetic error,
    zero while blocked, otherwise the invoice's own stated gross."""
    if decision == "BLOCKED":
        return Decimal("0.00")

    rule_ids = {f.rule_id for f in findings}
    vat_factor = Decimal(1) + invoice.vat_rate_pct / Decimal(100)

    if "ARITHMETIC_MISMATCH" in rule_ids:
        correct_net = round_eur(sum((l.line_total_eur for l in invoice.lines), Decimal("0.00")))
        return round_eur(correct_net * vat_factor)

    if "PRICE_DEVIATION" in rule_ids and po_lines:
        po_by_sku = {l.sku: l for l in po_lines}
        net_at_po = Decimal("0.00")
        for line in invoice.lines:
            po_line = po_by_sku.get(line.sku)
            price = po_line.unit_price_eur if po_line else line.unit_price_eur
            net_at_po += round_eur(price * line.qty)
        return round_eur(net_at_po * vat_factor)

    if "QTY_SHORT" in rule_ids:
        received_by_sku: dict[str, Decimal] = {}
        for gr in gr_lines:
            received_by_sku[gr.sku] = received_by_sku.get(gr.sku, Decimal(0)) + gr.qty_received
        net_at_received = Decimal("0.00")
        for line in invoice.lines:
            qty = min(line.qty, received_by_sku.get(line.sku, line.qty))
            net_at_received += round_eur(qty * line.unit_price_eur)
        return round_eur(net_at_received * vat_factor)

    return invoice.gross_eur


# --- orchestrator ------------------------------------------------------------

def match_invoice(
    invoice: Invoice,
    suppliers: dict[str, Supplier],
    purchase_orders: dict[str, list[POLine]],
    goods_receipts: dict[str, list[GoodsReceipt]],
    posted_invoices: list[PostedInvoice],
    cfg: dict,
    today: date,
) -> MatchResult:
    """Run every rule for one invoice and assemble the MatchResult."""
    supplier = find_supplier_by_vat(invoice.supplier_vat_id, suppliers)

    findings: list[Finding] = []
    findings += validate_invoice(
        invoice, Decimal(str(cfg["tolerance"]["total_abs_eur"])), _route(cfg, "ARITHMETIC_MISMATCH")
    )
    findings += rule_supplier_unknown(invoice, supplier, cfg)

    po_lines = purchase_orders.get(invoice.po_number) if invoice.po_number else None
    findings += rule_po_unknown(invoice, po_lines, cfg)

    gr_lines: list[GoodsReceipt] = []
    if po_lines:
        gr_lines = goods_receipts.get(invoice.po_number, [])
        gr_missing = rule_gr_missing(invoice, gr_lines, supplier, cfg, today)
        findings += gr_missing
        if not gr_missing:
            findings += rule_short_delivery(invoice, po_lines, gr_lines, cfg)
        findings += rule_price_tolerance(invoice, po_lines, cfg)

    dup_exact = rule_duplicate_exact(invoice, supplier, posted_invoices, cfg)
    findings += dup_exact
    if not dup_exact:
        findings += rule_duplicate_fuzzy(invoice, supplier, posted_invoices, cfg)

    findings += rule_iban_mismatch(invoice, supplier, cfg)

    decision = decide(findings)
    route_to = pick_route(findings, decision)
    payable_eur = _suggested_payable(invoice, po_lines, gr_lines, findings, decision)
    if decision == "BLOCKED":
        # Payment is blocked outright - a skonto deadline would imply "pay
        # by this date for a discount", which is misleading while blocked.
        skonto_eur, skonto_deadline, pay_by = Decimal("0.00"), None, None
    else:
        skonto_eur, skonto_deadline, pay_by = skonto.compute_skonto(payable_eur, supplier, invoice.invoice_date, today)
    processing_path = "auto" if decision in ("APPROVED", "HOLD") else "manual"

    return MatchResult(
        invoice=invoice, supplier=supplier, findings=findings, decision=decision,
        route_to=route_to, payable_eur=payable_eur, skonto_eur=skonto_eur,
        skonto_deadline=skonto_deadline, pay_by=pay_by, processing_path=processing_path,
    )
