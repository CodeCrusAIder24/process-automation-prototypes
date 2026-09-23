"""Parse simplified UBL e-invoices (XRechnung-style XML).

Since the E-Rechnungspflicht (mandatory e-invoicing for German B2B, phased
in from 2025), a growing share of supplier invoices arrive as structured
XML instead of PDF/text. Those never need the regex/LLM extractor at all -
the numbers are already machine-readable, so we read them straight with
xml.etree.ElementTree and mark `extraction_source="e-invoice"`.

The real UBL 2.1 Invoice schema is much larger than this; this module
implements only the subset our sample data uses (see data/invoices/*.xml),
which is enough to demonstrate the "skip extraction for structured
invoices" principle without pulling in a UBL library.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from .models import Invoice, InvoiceLine

NS = {
    "": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}


def _text(el: ET.Element, path: str) -> str | None:
    found = el.find(path, NS)
    return found.text.strip() if found is not None and found.text else None


def parse_einvoice(path: Path) -> Invoice:
    """Parse one simplified-UBL invoice XML file into an Invoice."""
    root = ET.parse(path).getroot()

    invoice_number = _text(root, "cbc:ID") or ""
    invoice_date = _text(root, "cbc:IssueDate") or ""
    po_number = _text(root, "cbc:OrderReference/cbc:ID")

    supplier_vat = _text(
        root, "cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID"
    ) or ""
    iban = _text(
        root, "cac:AccountingSupplierParty/cac:Party/cac:PayeeFinancialAccount/cbc:ID"
    ) or ""

    # Store code is embedded in the buyer party name ("... - Filiale XY1")
    # in our simplified schema; a real XRechnung would carry it as a
    # structured BuyerReference/DeliveryLocation instead.
    buyer_name = _text(root, "cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name") or ""
    store = buyer_name.rsplit("Filiale", 1)[-1].strip() if "Filiale" in buyer_name else None

    lines: list[InvoiceLine] = []
    for line_el in root.findall("cac:InvoiceLine", NS):
        sku = _text(line_el, "cac:Item/cac:SellersItemIdentification/cbc:ID") or ""
        desc = _text(line_el, "cac:Item/cbc:Description") or ""
        qty = Decimal(_text(line_el, "cbc:InvoicedQuantity") or "0")
        line_total = Decimal(_text(line_el, "cbc:LineExtensionAmount") or "0")
        unit_price = Decimal(_text(line_el, "cac:Price/cbc:PriceAmount") or "0")
        lines.append(InvoiceLine(sku=sku, description=desc, qty=qty,
                                  unit_price_eur=unit_price, line_total_eur=line_total))

    net = Decimal(_text(root, "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount") or "0")
    gross = Decimal(_text(root, "cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount") or "0")
    vat_amount = Decimal(_text(root, "cac:TaxTotal/cbc:TaxAmount") or "0")
    vat_pct_text = _text(root, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent")
    vat_rate_pct = Decimal(vat_pct_text) if vat_pct_text else Decimal("0")

    return Invoice(
        source_file=path.name,
        supplier_vat_id=supplier_vat,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        po_number=po_number,
        iban=iban,
        net_eur=net,
        vat_rate_pct=vat_rate_pct,
        vat_amount_eur=vat_amount,
        gross_eur=gross,
        lines=lines,
        store=store,
        extraction_source="e-invoice",
        extraction_note="Structured XML e-invoice, extraction skipped.",
    )
