"""Template-based supplier/store correspondence for exceptions.

Deliberately NOT LLM-generated: the wording needs to be predictable,
auditable and legally safe (never inventing a number that isn't already on
the MatchResult), matching the same "deterministic where it decides
something" principle as match.py. This is plain Python string
interpolation, nothing more.

These are real business letters to German suppliers, so - unlike
Finding.message, which stays English (report-facing) with German terms in
parentheses - every draft here is written entirely in German. To keep that
true without parsing English sentences back apart, drafts are built from
each Finding's `details` dict (structured numbers/SKUs/dates), never from
`finding.message`. All amounts go through formatting.fmt_eur and all dates
through formatting.fmt_de_date, so "1.234,56 EUR" / "24.09.2026" are the
only formats a supplier ever sees.

No draft is generated for IBAN_MISMATCH on purpose - the correct response
to a suspected bank-detail fraud is a phone call using the number on file,
never a written reply to the address on the (possibly fraudulent) invoice.
Nor for GR_NOT_POSTED / HOLD, which needs no human correspondence at all.
"""
from __future__ import annotations

from pathlib import Path

from .formatting import fmt_de_date, fmt_eur
from .models import Finding, MatchResult

_LETTERHEAD = (
    "Brandt Haus & Garten GmbH - Kreditorenbuchhaltung\n"
    "Speicherstadt 1, 20457 Hamburg\n"
    "--------------------------------------------------\n\n"
)


def _price_deviation_detail_lines(details: dict) -> str:
    lines = []
    for l in details.get("lines", []):
        lines.append(
            f"  Art.-Nr. {l['sku']}: berechnet {fmt_eur(l['invoiced_price'])}/Stück, "
            f"vereinbart laut Bestellung {fmt_eur(l['po_price'])}/Stück (Menge {l['qty']} Stück)"
        )
    return "\n".join(lines)


def _short_delivery_detail_lines(details: dict) -> str:
    lines = []
    for l in details.get("lines", []):
        lines.append(
            f"  Art.-Nr. {l['sku']}: berechnet {l['invoiced_qty']} Stück, "
            f"geliefert {l['received_qty']} Stück (Differenz {l['shortfall']})"
        )
    return "\n".join(lines)


def _arithmetic_detail_lines(details: dict) -> str:
    lines = []
    if details.get("net_diff", 0) > 0:
        lines.append(
            f"  Positionssumme {fmt_eur(details['line_sum'])} ergibt nicht den angegebenen "
            f"Nettobetrag {fmt_eur(details['stated_net'])} (Differenz {fmt_eur(details['net_diff'])})"
        )
    if details.get("gross_diff", 0) > 0:
        lines.append(
            f"  Nettobetrag zzgl. USt ergibt {fmt_eur(details['expected_gross'])}, angegeben wurde "
            f"jedoch {fmt_eur(details['stated_gross'])} (Differenz {fmt_eur(details['gross_diff'])})"
        )
    return "\n".join(lines)


def _duplicate_detail_lines(details: dict, rule_id: str) -> str:
    if rule_id == "DUPLICATE_EXACT":
        return (
            f"  Diese Rechnung wurde bereits am {fmt_de_date(details['paid_date'])} beglichen "
            f"(Betrag {fmt_eur(details['gross_eur'])})."
        )
    return (
        f"  Gleicher Lieferant und gleicher Bruttobetrag ({fmt_eur(details['gross_eur'])}) wie die bereits "
        f"bezahlte Rechnung {details['other_invoice_number']} (bezahlt am {fmt_de_date(details['paid_date'])}), "
        f"{details['days_apart']} Tag(e) auseinander."
    )


def _price_deviation_draft(r: MatchResult, finding: Finding) -> str:
    inv = r.invoice
    return (
        f"{_LETTERHEAD}An: {r.supplier.name if r.supplier else inv.supplier_vat_id}\n"
        f"Betreff: Preisabweichung Rechnung {inv.invoice_number} vom {fmt_de_date(inv.invoice_date)}\n\n"
        "Sehr geehrte Damen und Herren,\n\n"
        f"bei der Prüfung Ihrer Rechnung {inv.invoice_number} zu unserer Bestellung {inv.po_number} "
        "haben wir festgestellt, dass der berechnete Einzelpreis auf mindestens einer Position von "
        "unserer Bestellung abweicht:\n\n"
        f"{_price_deviation_detail_lines(finding.details)}\n\n"
        f"Wir bitten um eine Gutschrift über die Differenz. Zur Zahlung vorgesehen ist der Betrag "
        f"zum Bestellpreis: {fmt_eur(r.payable_eur)} (statt berechneter {fmt_eur(inv.gross_eur)}).\n\n"
        "Mit freundlichen Grüßen\nKreditorenbuchhaltung, Brandt Haus & Garten GmbH\n"
    )


def _short_delivery_draft(r: MatchResult, finding: Finding) -> str:
    inv = r.invoice
    return (
        f"{_LETTERHEAD}An: {r.supplier.name if r.supplier else inv.supplier_vat_id}\n"
        f"Betreff: Mindermenge Rechnung {inv.invoice_number} vom {fmt_de_date(inv.invoice_date)}\n\n"
        "Sehr geehrte Damen und Herren,\n\n"
        f"laut Wareneingang in unserer Filiale wurde zu Ihrer Rechnung {inv.invoice_number} "
        f"(Bestellung {inv.po_number}) eine geringere Menge geliefert als berechnet:\n\n"
        f"{_short_delivery_detail_lines(finding.details)}\n\n"
        f"Wir bitten um eine Gutschrift für die nicht gelieferte Menge. Zur Zahlung vorgesehen ist "
        f"der Betrag zur tatsächlich erhaltenen Menge: {fmt_eur(r.payable_eur)} (statt berechneter "
        f"{fmt_eur(inv.gross_eur)}).\n\n"
        "Mit freundlichen Grüßen\nKreditorenbuchhaltung, Brandt Haus & Garten GmbH\n"
    )


def _arithmetic_draft(r: MatchResult, finding: Finding) -> str:
    inv = r.invoice
    return (
        f"{_LETTERHEAD}An: {r.supplier.name if r.supplier else inv.supplier_vat_id}\n"
        f"Betreff: Rechnung {inv.invoice_number} vom {fmt_de_date(inv.invoice_date)} - Bitte um Korrektur\n\n"
        "Sehr geehrte Damen und Herren,\n\n"
        f"bei der Prüfung Ihrer Rechnung {inv.invoice_number} haben wir festgestellt, dass die "
        f"Rechnung rechnerisch nicht korrekt ist:\n\n"
        f"{_arithmetic_detail_lines(finding.details)}\n\n"
        "Wir bitten um eine korrigierte Rechnung. Eine Zahlung kann erst nach Erhalt der "
        "korrigierten Rechnung erfolgen.\n\n"
        "Mit freundlichen Grüßen\nKreditorenbuchhaltung, Brandt Haus & Garten GmbH\n"
    )


def _duplicate_draft(r: MatchResult, finding: Finding) -> str:
    inv = r.invoice
    return (
        f"{_LETTERHEAD}An: {r.supplier.name if r.supplier else inv.supplier_vat_id}\n"
        f"Betreff: Rechnung {inv.invoice_number} - bereits beglichen\n\n"
        "Sehr geehrte Damen und Herren,\n\n"
        f"vielen Dank für die erneute Zusendung der Rechnung {inv.invoice_number}. Diese Rechnung "
        f"wurde von uns bereits beglichen.\n\n"
        f"{_duplicate_detail_lines(finding.details, finding.rule_id)}\n\n"
        "Eine erneute Zahlung wird nicht veranlasst. Sollten Sie der Meinung sein, dass diese "
        "Rechnung noch offen ist, wenden Sie sich bitte mit dem Zahlungsdatum an uns.\n\n"
        "Mit freundlichen Grüßen\nKreditorenbuchhaltung, Brandt Haus & Garten GmbH\n"
    )


def _po_unknown_draft(r: MatchResult, finding: Finding) -> str:
    inv = r.invoice
    po_ref = finding.details.get("po_ref") or "(keine Angabe)"
    return (
        f"{_LETTERHEAD}Interner Vermerk - keine Weiterleitung an den Lieferanten\n\n"
        f"Rechnung {inv.invoice_number} vom {fmt_de_date(inv.invoice_date)} von "
        f"{r.supplier.name if r.supplier else inv.supplier_vat_id} referenziert die "
        f"Bestellnummer {po_ref}, die im ERP nicht bekannt ist.\n\n"
        "Bitte in der Filiale/beim Fachbereich klären, wer diese Bestellung ohne Bestellnummer ausgelöst "
        "hat (Maverick Buying), und nachträglich eine Bestellung anlegen bzw. freigeben, bevor die "
        "Rechnung bezahlt wird.\n"
    )


def _supplier_unknown_draft(r: MatchResult, finding: Finding) -> str:
    inv = r.invoice
    return (
        f"{_LETTERHEAD}Interner Vermerk - keine Weiterleitung an den Lieferanten\n\n"
        f"Rechnung {inv.invoice_number} vom {fmt_de_date(inv.invoice_date)} nennt die USt-IdNr. "
        f"{finding.details.get('vat_id', '-')}, die keinem Lieferanten in den Stammdaten zugeordnet ist.\n\n"
        "Bitte Lieferantenstammdaten prüfen/anlegen, bevor die Rechnung bezahlt wird.\n"
    )


# rule_id -> drafting function. Rules not listed here (IBAN_MISMATCH,
# GR_NOT_POSTED, PRICE_WITHIN_TOLERANCE) get no draft.
_DRAFTERS = {
    "PRICE_DEVIATION": _price_deviation_draft,
    "QTY_SHORT": _short_delivery_draft,
    "ARITHMETIC_MISMATCH": _arithmetic_draft,
    "DUPLICATE_EXACT": _duplicate_draft,
    "DUPLICATE_FUZZY": _duplicate_draft,
    "PO_UNKNOWN": _po_unknown_draft,
    "SUPPLIER_UNKNOWN": _supplier_unknown_draft,
}


def draft_for(r: MatchResult) -> str | None:
    """One draft per MatchResult, based on its highest-priority finding
    that has a drafter registered (BLOCKED IBAN mismatches intentionally
    fall through to None - see module docstring)."""
    for finding in r.findings:
        drafter = _DRAFTERS.get(finding.rule_id)
        if drafter is None:
            continue
        return drafter(r, finding)
    return None


def write_drafts(results: list[MatchResult], out_dir: Path) -> list[Path]:
    """Write out/drafts/{invoice_number}.txt for every result that has a
    draft. Returns the paths written."""
    drafts_dir = out_dir / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for r in results:
        text = draft_for(r)
        if text is None:
            continue
        path = drafts_dir / f"{r.invoice.invoice_number}.txt"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written
