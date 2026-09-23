"""Console output, results.json, worklist.csv and the self-contained
out/report.html dashboard.

out/report.html shares its exact design system with portfolio piece #1
(Mail Triage Agent, triage/report.py -> out/report.html): the same
tokens.css, the same Google Fonts request (FONTS_HREF, imported from
triage.report so there is one source of truth), and as much of the same
CSS class vocabulary as the content allows (nav/hero/flow/stats/bars/band/
table-filter/foot-line). This is a deliberate, user-directed exception to
Hallmark's usual per-page diversification rule: sibling portfolio pieces
are meant to read as one system, the same way Hallmark treats a
design.md-managed multi-page app. See the stamp at the top of REPORT_CSS.

Console output and results.json/worklist.csv stay English (machine-
readable / AP-tool-import shaped); the HTML report is German UI throughout,
matching piece #1's out/report.html.
"""
from __future__ import annotations

import csv
import html
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import kpi as kpi_mod
from .formatting import fmt_de_date, fmt_de_number, fmt_eur, fmt_pct
from .models import MatchResult
from .skonto import parse_iso

# triage/report.py lives at the repo root, one level above invoice_match/ -
# add it to sys.path so FONTS_HREF has exactly one source of truth across
# both portfolio pieces, however this module is imported (run_match.py from
# invoice_match/, or `python invoice_match/run_match.py` from the repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from triage.report import FONTS_HREF  # noqa: E402

TOKENS_PATH = _REPO_ROOT / "tokens.css"

_FALLBACK_TOKENS = """
:root{--color-paper:#f7f8fa;--color-ink:#1c2430;--color-muted:#5b6472;--color-rule:#d9dee6;
--color-accent:#2554c7;--color-accent-ink:#fff;--color-status-good:#1f9d55;--color-status-human:#c9752b;
--color-status-quarantine:#c0392b;--color-status-draft:#a98600;--font-body:system-ui,sans-serif;
--font-display:system-ui,sans-serif;--font-mono:ui-monospace,monospace;--radius-card:10px;--radius-control:6px;}
"""


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


# Local alias kept so the rest of this module reads exactly like piece #1's
# triage/report.py (which defines its own _de) while actually sharing the
# implementation via apmatch/formatting.py - one German-number formatter
# for the whole codebase, not two copies that can drift apart.
_de = fmt_de_number


# --- console (English) ---------------------------------------------------------

DECISION_LABEL = {
    "APPROVED": "APPROVED",
    "EXCEPTION": "EXCEPTION",
    "HOLD": "HOLD",
    "BLOCKED": "BLOCKED",
}


def print_console_table(results: list[MatchResult], today, extraction_mode: str, rules_path: Path) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # Route for an APPROVED invoice ("Automatisch verbucht (kein Review
    # erforderlich)") is far longer than any exception route and would
    # just get truncated - show a short marker instead. For everything
    # else, size the column to the longest route actually appearing in
    # this batch (e.g. "Finance Lead (Vier-Augen-Prinzip)") so nothing
    # gets cut off.
    route_display = {
        r.invoice.source_file: ("- (auto-posted)" if r.decision == "APPROVED" else r.route_to)
        for r in results
    }
    route_width = max((len(v) for v in route_display.values()), default=10) + 2

    print(f"Invoice Match - Brandt Haus & Garten GmbH  |  today={today}  "
          f"extraction={extraction_mode}  rules={rules_path.name}")
    width = 92 + route_width
    print("-" * width)
    print(f"{'File':<22}{'Supplier':<26}{'Gross':>14}  {'Decision':<10}{'Route':<{route_width}}Reason")
    print("-" * width)
    for r in results:
        supplier_name = r.supplier.name if r.supplier else r.invoice.supplier_vat_id
        reason = r.findings[0].message if r.findings else "-"
        if len(reason) > 60:
            reason = reason[:57] + "..."
        print(
            f"{r.invoice.source_file:<22}{supplier_name[:25]:<26}{fmt_eur(r.invoice.gross_eur):>14}  "
            f"{DECISION_LABEL[r.decision]:<10}{route_display[r.invoice.source_file]:<{route_width}}{reason}"
        )
    print("-" * width)


def print_kpi_block(batch: dict, extrap: dict, skonto_year: str) -> None:
    print("KPIs (batch facts, then a clearly-labelled monthly/yearly estimate):")
    print(f"  Invoices processed:        {batch['total_invoices']}")
    print(f"  Touchless (APPROVED):      {batch['approved']} ({batch['touchless_rate_pct']}%)")
    print(f"  Exceptions / Holds / Blocked: {batch['exceptions']} / {batch['holds']} / {batch['blocked']}")
    print(f"  Money at risk caught:      {fmt_eur(batch['money_at_risk_total_eur'])}")
    print(f"  Skonto captured (batch):   {fmt_eur(batch['skonto_captured_eur'])}")
    print(f"  Est. hours/month:          {extrap['hours_before_per_month']} h -> {extrap['hours_after_per_month']} h "
          f"(saved {extrap['hours_saved_per_month']} h, ~{extrap['fte_equivalent']} FTE, "
          f"~{fmt_eur(extrap['cost_saved_eur_per_month'])}/month)")
    print(f"  Est. skonto recovered/year: {fmt_eur(skonto_year)}")


# --- machine-readable outputs --------------------------------------------------

def write_results_json(
    results: list[MatchResult], batch: dict, extrap: dict, skonto_year: str,
    today: str, extraction_mode: str, out_dir: Path,
) -> Path:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "today": today,
        "extraction_mode": extraction_mode,
        "batch_kpis": batch,
        "extrapolation": extrap,
        "skonto_recovered_per_year_eur": skonto_year,
        "results": [r.to_dict() for r in results],
    }
    path = out_dir / "results.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_worklist_csv(results: list[MatchResult], out_dir: Path) -> Path:
    """Exceptions/holds/blocks only - the import format an AP tool or
    ticket system would take."""
    path = out_dir / "worklist.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["invoice", "supplier", "decision", "route_to", "reason",
                    "suggested_action", "payable", "skonto_deadline"])
        for r in results:
            if r.decision == "APPROVED":
                continue
            reason = "; ".join(f.message for f in r.findings) or "-"
            action = "; ".join(f.suggested_action for f in r.findings) or "-"
            w.writerow([
                r.invoice.source_file,
                r.supplier.name if r.supplier else r.invoice.supplier_vat_id,
                r.decision, r.route_to, reason, action, fmt_eur(r.payable_eur),
                r.skonto_deadline or "",
            ])
    return path


# --- HTML dashboard (German UI, piece-#1 design system) ------------------------

# Inline SVG favicon: same construction as triage/report.py's (a graphite
# rounded square behind a miniature flow bar) but coloured for this page's
# four decision families instead of piece #1's four mail-decision families.
# Data-URI favicons can't consume CSS custom properties, so the OKLCH values
# are the literal values tokens.css assigns to --color-status-good/draft/
# human/quarantine - not new colours, just the same tokens spelled out
# (see tokens.css for the source of truth).
FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='7' fill='oklch(22%25 0.016 260)'/%3E"
    "%3Crect x='4' y='12' width='9' height='8' rx='1.5' fill='oklch(60%25 0.17 145)'/%3E"
    "%3Crect x='14' y='12' width='4' height='8' rx='1.5' fill='oklch(80%25 0.16 85)'/%3E"
    "%3Crect x='19' y='12' width='5' height='8' rx='1.5' fill='oklch(70%25 0.16 45)'/%3E"
    "%3Crect x='25' y='12' width='4' height='8' rx='1.5' fill='oklch(48%25 0.2 25)'/%3E"
    "%3C/svg%3E"
)

# Decision families for the flow bar and the table filter, left to right -
# matches the locked section order (Freigegeben / Wiedervorlage / Klärung /
# Gestoppt). Mirrors triage/report.py's FLOW_FAMILIES pattern exactly, one
# family per MatchResult.decision value.
FLOW_FAMILIES = [
    ("freigegeben", "Freigegeben", {"APPROVED"}),
    ("wiedervorlage", "Wiedervorlage", {"HOLD"}),
    ("klaerung", "Klärung", {"EXCEPTION"}),
    ("gestoppt", "Gestoppt", {"BLOCKED"}),
]
DECISION_FAMILY = {d: fam for fam, _, decisions in FLOW_FAMILIES for d in decisions}

# German rule labels for the report only (finding.message / match.RULE_LABELS
# stay English-with-German-in-parens for console/worklist.csv/drafts - this
# map is the report's pure-German vocabulary, per the locked decisions).
DE_RULE_LABELS = {
    "SUPPLIER_UNKNOWN": "Lieferant unbekannt",
    "PO_UNKNOWN": "Bestellung unbekannt",
    "PRICE_DEVIATION": "Preisabweichung",
    "PRICE_WITHIN_TOLERANCE": "Preisabweichung (Toleranz)",
    "QTY_SHORT": "Mindermenge",
    "GR_NOT_POSTED": "Wareneingang fehlt",
    "DUPLICATE_EXACT": "Doppelrechnung",
    "DUPLICATE_FUZZY": "Doppelrechnung (Verdacht)",
    "DUPLICATE": "Doppelrechnung",
    "IBAN_MISMATCH": "Geänderte Bankverbindung (IBAN)",
    "ARITHMETIC_MISMATCH": "Rechenfehler",
}


def _de_rule_label(rule_id: str) -> str:
    return DE_RULE_LABELS.get(rule_id, rule_id)


def _de_reason(f) -> str:
    """One-line German reason text for the HTML report, built from the
    Finding's structured `details` dict - the same source drafts.py uses
    for the German supplier letters. Never reads `finding.message`, which
    stays English (console / worklist.csv / results.json only), per the
    locked decision that the report's UI is German throughout."""
    d = f.details
    if f.rule_id in ("PRICE_DEVIATION", "PRICE_WITHIN_TOLERANCE"):
        parts = [
            f"{_esc(l['sku'])}: {fmt_eur(l['invoiced_price'])} statt {fmt_eur(l['po_price'])}/Stück "
            f"({fmt_de_number(l['deviation_pct'], 1)}&nbsp;%)"
            for l in d.get("lines", [])
        ]
        return "; ".join(parts) or _de_rule_label(f.rule_id)
    if f.rule_id == "QTY_SHORT":
        parts = [
            f"{_esc(l['sku'])}: {l['invoiced_qty']} berechnet, {l['received_qty']} geliefert (fehlen {l['shortfall']})"
            for l in d.get("lines", [])
        ]
        return "; ".join(parts) or _de_rule_label(f.rule_id)
    if f.rule_id == "ARITHMETIC_MISMATCH":
        parts = []
        if float(d.get("net_diff", 0) or 0) > 0:
            parts.append(f"Positionssumme {fmt_eur(d['line_sum'])} statt Nettobetrag {fmt_eur(d['stated_net'])}")
        if float(d.get("gross_diff", 0) or 0) > 0:
            parts.append(f"Bruttobetrag {fmt_eur(d['stated_gross'])} statt berechneter {fmt_eur(d['expected_gross'])}")
        return "; ".join(parts) or _de_rule_label(f.rule_id)
    if f.rule_id in ("DUPLICATE_EXACT", "DUPLICATE_FUZZY"):
        paid = d.get("paid_date")
        base = f"bereits bezahlt am {fmt_de_date(paid)}" if paid else "bereits bezahlt"
        if f.rule_id == "DUPLICATE_FUZZY" and d.get("other_invoice_number"):
            base += f" als Rechnung {_esc(d['other_invoice_number'])}"
        return base
    if f.rule_id == "IBAN_MISMATCH":
        return "IBAN weicht von den Lieferantenstammdaten ab"
    if f.rule_id == "PO_UNKNOWN":
        ref = d.get("po_ref")
        return f"Bestellnummer {_esc(ref)!s} unbekannt" if ref else "Keine Bestellnummer angegeben"
    if f.rule_id == "SUPPLIER_UNKNOWN":
        return f"USt-IdNr. {_esc(d.get('vat_id', '-'))} keinem Lieferanten zugeordnet"
    if f.rule_id == "GR_NOT_POSTED":
        return "Wareneingang noch nicht gebucht"
    return _de_rule_label(f.rule_id)


# German imperative action per rule_id, for the worklist cards - the German
# counterpart to Finding.suggested_action (which stays English, same reason
# as finding.message: console/worklist.csv/results.json read it as-is).
DE_ACTION_LABELS = {
    "SUPPLIER_UNKNOWN": "Lieferantenstammdaten prüfen/anlegen vor Zahlung.",
    "PO_UNKNOWN": "Besteller ermitteln, Bestellung nachträglich anlegen bzw. freigeben.",
    "PRICE_DEVIATION": "Gutschrift für die Preisdifferenz anfordern.",
    "QTY_SHORT": "Menge mit der Filiale abstimmen, Gutschrift für die Fehlmenge anfordern.",
    "GR_NOT_POSTED": "Automatische Wiedervorlage, sobald der Wareneingang gebucht ist.",
    "DUPLICATE_EXACT": "Nicht zahlen. Lieferant über das Zahlungsdatum informieren.",
    "DUPLICATE_FUZZY": "Vor Zahlung mit dem Lieferanten klären - vermutlich dieselbe Lieferung.",
    "IBAN_MISMATCH": ("Bankänderung telefonisch mit der Nummer aus den Stammdaten verifizieren - "
                       "nie mit Kontaktdaten von der Rechnung."),
    "ARITHMETIC_MISMATCH": "Korrigierte Rechnung anfordern.",
}


def _de_action(f) -> str:
    return DE_ACTION_LABELS.get(f.rule_id, _esc(f.suggested_action))


DECISION_LABEL_DE = {"APPROVED": "Freigegeben", "HOLD": "Wiedervorlage",
                      "EXCEPTION": "Klärung", "BLOCKED": "Gestoppt"}

SEVERITY_TIER_ORDER = ["BLOCKED", "EXCEPTION", "HOLD"]
SEVERITY_TIER_LABEL = {
    "BLOCKED": "Gestoppt", "EXCEPTION": "Klärung erforderlich", "HOLD": "Automatische Wiedervorlage",
}


def _family_counts(results: list[MatchResult]) -> dict[str, int]:
    counts = {fam: 0 for fam, _, _ in FLOW_FAMILIES}
    for r in results:
        counts[DECISION_FAMILY.get(r.decision, "klaerung")] += 1
    return counts


def _flow_bar(results: list[MatchResult]) -> str:
    """Mirrors triage/report.py's _flow_bar: one aria-label on the container
    carries every segment's count (critique P2), and - going one step
    further than piece #1 - each segment also carries its own visually-
    hidden text, in case assistive tech traverses into a role="img"
    container despite the label (belt-and-braces per the locked spec)."""
    total = len(results) or 1
    counts = _family_counts(results)
    segments, legend, spoken = [], [], []
    for fam, label, _ in FLOW_FAMILIES:
        n = counts[fam]
        if n == 0:
            continue
        pct = n / total * 100
        spoken_segment = f"{label}: {n} von {total} Rechnungen"
        segments.append(
            f'<div class="seg seg-{fam}" style="flex-basis:{pct:.2f}%" title="{_esc(spoken_segment)}">'
            f'<span class="tnum" aria-hidden="true">{n}</span>'
            f'<span class="sr-only">{_esc(spoken_segment)}</span></div>'
        )
        legend.append(f'<li><i class="dot dot-{fam}"></i>{_esc(label)}<b class="tnum">{n}</b></li>')
        spoken.append(f"{n} {label}")
    aria = f"Verteilung der {total} Rechnungen: " + ", ".join(spoken) + "."
    return (
        f'<div class="flow" role="img" aria-label="{_esc(aria)}">' + "".join(segments) + "</div>"
        + '<ul class="legend">' + "".join(legend) + "</ul>"
    )


def _stat_strip(batch: dict, extrap: dict, skonto_year: str) -> str:
    items = [
        (fmt_eur(batch["money_at_risk_total_eur"]), "Risiko abgefangen",
         "in diesem Lauf, siehe Aufschlüsselung unten"),
        (fmt_eur(batch["skonto_captured_eur"]), "Skonto gesichert (Batch)",
         f"geschätzt {fmt_eur(skonto_year)} / Jahr"),
        (f"{_de(batch['touchless_rate_pct'], 0)}&nbsp;%", "Dunkelverarbeitung",
         f"{batch['approved']} / {batch['total_invoices']} Rechnungen automatisch freigegeben"),
        (f"{_de(extrap['hours_saved_per_month'], 0)}&nbsp;h", "Zeitersparnis / Monat",
         f"geschätzt, ~{fmt_de_number(extrap['fte_equivalent'], 2)} FTE"),
    ]
    out = []
    for value, label, note in items:
        out.append(f'<li><span class="stat__value tnum">{value}</span>'
                   f'<span class="stat__label">{label}</span><span class="stat__note">{note}</span></li>')
    return '<ol class="stats">' + "".join(out) + "</ol>"


def _before_after_hours(extrap: dict) -> str:
    before, after = float(extrap["hours_before_per_month"]), float(extrap["hours_after_per_month"])
    after_pct = (after / before * 100) if before else 0
    return f"""
<div class="bars">
  <div class="bar-row">
    <div class="bar-label">Heute, manuell</div>
    <div class="bar-track"><div class="bar-fill fill-neutral" style="--w:100%"></div></div>
    <div class="bar-value tnum">{_de(before)}&nbsp;h</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Mit Invoice Match</div>
    <div class="bar-track"><div class="bar-fill fill-accent" style="--w:{after_pct:.1f}%"></div></div>
    <div class="bar-value tnum">{_de(after)}&nbsp;h</div>
  </div>
</div>
<p class="note">Geschätzt bei {extrap['invoices_per_month']} Rechnungen/Monat. <a href="#annahmen">Annahmen ansehen</a></p>"""


def _before_after_steps() -> str:
    """9 -> 0 manual steps for a clean (touchless) invoice - see
    kpi.clean_invoice_step_counts() for why it's 0, not
    len(kpi.AFTER_STEPS_TOUCHLESS); this is just the headline bar, the
    full step lists render under #funktionsweise."""
    before_n, after_n, removed_n = kpi_mod.clean_invoice_step_counts()
    return f"""
<div class="bars">
  <div class="bar-row">
    <div class="bar-label">Manuelle Schritte heute</div>
    <div class="bar-track"><div class="bar-fill fill-neutral" style="--w:100%"></div></div>
    <div class="bar-value tnum">{before_n}</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Je unauffälliger Rechnung</div>
    <div class="bar-track"><div class="bar-fill fill-accent" style="--w:{(after_n / before_n * 100) if before_n else 0:.1f}%"></div></div>
    <div class="bar-value tnum">{after_n}</div>
  </div>
</div>
<p class="note">{removed_n} von {before_n} manuellen Schritten entfallen bei einer sauberen Rechnung.</p>"""


def _money_at_risk_bars(batch: dict) -> str:
    by_rule = batch["money_at_risk_by_rule"]
    if not by_rule:
        return '<p class="note">Keine Risikobefunde in diesem Lauf.</p>'
    values = {k: float(v) for k, v in by_rule.items()}
    max_v = max(values.values()) or 1
    rows = []
    for rule, amount in by_rule.items():
        w = values[rule] / max_v * 100
        rows.append(f"""
  <div class="bar-row">
    <div class="bar-label">{_esc(_de_rule_label(rule))}</div>
    <div class="bar-track"><div class="bar-fill fill-soft" style="--w:{w:.1f}%"></div></div>
    <div class="bar-value tnum">{_esc(fmt_eur(amount))}</div>
  </div>""")
    return '<div class="bars">' + "".join(rows) + "</div>"


DECISION_BADGE_CLASS = {
    "APPROVED": "dot-freigegeben", "EXCEPTION": "dot-klaerung",
    "HOLD": "dot-wiedervorlage", "BLOCKED": "dot-gestoppt",
}


def _badge(decision: str) -> str:
    fam_class = DECISION_BADGE_CLASS.get(decision, "")
    return f'<span class="badge"><i class="dot {fam_class}"></i>{_esc(DECISION_LABEL_DE.get(decision, decision))}</span>'


def _gestoppt_section(results: list[MatchResult]) -> str:
    """Analogue of triage/report.py's _threats(): the BLOCKED cases only,
    with the amount protected front and centre so a time-pressed recruiter
    sees the payment-diversion catch without scrolling past it."""
    blocked = [r for r in results if r.decision == "BLOCKED"]
    if not blocked:
        return '<p class="note">In diesem Lauf wurde keine Zahlung gestoppt.</p>'
    items = []
    for r in blocked:
        critical = [f for f in r.findings if f.severity == "critical"]
        chips = "".join(
            f'<li><code>{_esc(f.rule_id)}</code>{_esc(_de_rule_label(f.rule_id))}: {_de_reason(f)}</li>'
            for f in critical
        )
        items.append(f"""
  <li class="threat">
    <div class="threat-head">
      <span class="mail-id mono">{_esc(r.invoice.source_file)}</span>
      <span class="threat-subject">{_esc(r.supplier.name if r.supplier else r.invoice.supplier_vat_id)}</span>
      <span class="threat-from">Betrag geschützt: {_esc(fmt_eur(r.invoice.gross_eur))} &middot; {_esc(r.route_to)}</span>
    </div>
    <ul class="chips">{chips}</ul>
  </li>""")
    return '<ol class="threats">' + "".join(items) + "</ol>"


def _deadline_html(deadline_iso: str | None, today: str) -> str:
    """Distinct, not-colour-only urgency styling for a Skonto deadline that
    falls today or tomorrow (critique: must not read as a routine note).
    Anything further out is a plain note; today/tomorrow get their own
    labelled, coloured marker using the --color-urgent token added for
    this page (see REPORT_CSS's token block)."""
    if not deadline_iso:
        return ""
    try:
        days_left = (parse_iso(deadline_iso) - parse_iso(today)).days
    except ValueError:
        days_left = None
    if days_left == 0:
        return f'<strong class="deadline deadline--today">Skonto heute fällig ({_esc(fmt_de_date(deadline_iso))})</strong>'
    if days_left == 1:
        return f'<strong class="deadline deadline--tomorrow">Skonto morgen fällig ({_esc(fmt_de_date(deadline_iso))})</strong>'
    return f'<span class="sub">Skonto-Frist: {_esc(fmt_de_date(deadline_iso))}</span>'


def _worklist_section(results: list[MatchResult], today: str) -> str:
    exceptions = [r for r in results if r.decision != "APPROVED"]
    if not exceptions:
        return '<p class="note">Keine offenen Fälle in diesem Lauf - alles automatisch freigegeben.</p>'

    from .drafts import draft_for  # local import: report must not require drafts at module load

    tiers_html = []
    for tier in SEVERITY_TIER_ORDER:
        tier_results = [r for r in exceptions if r.decision == tier]
        if not tier_results:
            continue

        by_route: dict[str, list[MatchResult]] = {}
        for r in tier_results:
            by_route.setdefault(r.route_to, []).append(r)

        route_groups = []
        for route, items in sorted(by_route.items()):
            item_html = []
            for r in items:
                reasons = "".join(f"<li>{_de_reason(f)}</li>" for f in r.findings)
                actions = "".join(f"<li>{_de_action(f)}</li>" for f in r.findings)
                draft_text = draft_for(r)
                draft_html = ""
                if draft_text:
                    draft_html = (
                        "<details><summary>Entwurf anzeigen (Deutsch)</summary>"
                        f"<pre class='draft'>{_esc(draft_text)}</pre></details>"
                    )
                deadline_html = _deadline_html(r.skonto_deadline, today)
                item_html.append(f"""
      <li class="case">
        <div class="threat-head">
          <span class="mail-id mono">{_esc(r.invoice.source_file)}</span>
          <span class="threat-subject">{_esc(r.supplier.name if r.supplier else r.invoice.supplier_vat_id)}</span>
          <span class="threat-from">Zahlbetrag: {_esc(fmt_eur(r.payable_eur))}{(" &middot; " + deadline_html) if deadline_html else ""}</span>
        </div>
        <ul class="reasons">{reasons}</ul>
        <p class="sub"><strong>Empfehlung:</strong></p>
        <ul class="reasons">{actions}</ul>
        {draft_html}
      </li>""")
            route_groups.append(
                f'<div class="role-group"><h4>{_esc(route)} <span class="tnum">({len(items)})</span></h4>'
                f'<ol class="threats">{"".join(item_html)}</ol></div>'
            )
        tiers_html.append(
            f'<div class="severity-tier">'
            f'<h3 class="tier-heading"><i class="dot dot-{DECISION_BADGE_CLASS[tier].split("-", 1)[1]}"></i>'
            f'{_esc(SEVERITY_TIER_LABEL[tier])} <span class="tnum">({len(tier_results)})</span></h3>'
            f'{"".join(route_groups)}</div>'
        )
    return "".join(tiers_html)


def _filter_controls(results: list[MatchResult]) -> tuple[str, str]:
    """Radio inputs must be siblings of .table-scroll for the CSS-only
    filter - mirrors triage/report.py's _filter_controls exactly."""
    counts = _family_counts(results)
    total = len(results)
    inputs = ['<input class="filter-input" type="radio" name="fam" id="f-all" checked>']
    labels = [f'<label class="chip" for="f-all">Alle<b class="tnum">{total}</b></label>']
    for fam, label, _ in FLOW_FAMILIES:
        n = counts[fam]
        if n == 0:
            continue
        inputs.append(f'<input class="filter-input" type="radio" name="fam" id="f-{fam}">')
        labels.append(f'<label class="chip" for="f-{fam}"><i class="dot dot-{fam}"></i>{_esc(label)}<b class="tnum">{n}</b></label>')
    return "".join(inputs), "".join(labels)


def _route_cell(r: MatchResult) -> str:
    """Every APPROVED row carries the identical long route_to string
    ("Automatisch verbucht (kein Review erforderlich)") - repeating that
    verbatim in a table meant to be scanned is noise, not information.
    Show a short, muted marker instead; exceptions/holds/blocks keep their
    real routed-to role, which is exactly what's worth scanning for."""
    if r.decision == "APPROVED":
        return '<span class="sub">automatisch verbucht</span>'
    return _esc(r.route_to)


def _all_invoices_table(results: list[MatchResult]) -> str:
    rows = []
    for r in results:
        fam = DECISION_FAMILY.get(r.decision, "klaerung")
        # Capped via CSS (text-overflow: ellipsis on .col-why), not by
        # slicing the string here - _de_reason() can return HTML entities
        # (&nbsp;) and slicing by character count risks cutting one in
        # half, which would render as literal "&nbsp" text.
        reason = _de_reason(r.findings[0]) if r.findings else "-"
        rows.append(f"""
      <tr class="fam-{fam}">
        <td class="col-file nowrap mono">{_esc(r.invoice.source_file)}</td>
        <td>{_esc(r.supplier.name if r.supplier else r.invoice.supplier_vat_id)}</td>
        <td class="num nowrap">{_esc(fmt_eur(r.invoice.gross_eur))}</td>
        <td>{_badge(r.decision)}</td>
        <td>{_route_cell(r)}</td>
        <td class="col-why">{reason}</td>
      </tr>""")
    return "".join(rows)


def _rules_de_band(rules_cfg: dict) -> str:
    tol = rules_cfg["tolerance"]
    dup = rules_cfg["duplicates"]
    hold = rules_cfg["hold"]
    rows = [
        (f"{_de(tol['price_pct'], 1)}&nbsp;%", "Preistoleranz je Position", "darüber: Klärung beim Einkauf, Zahlung zum Bestellpreis vorgeschlagen."),
        (fmt_eur(tol["total_abs_eur"]), "Rechnerische Toleranz", "Positionssumme vs. Nettobetrag, Netto+USt vs. Bruttobetrag."),
        (f"{tol['qty_tolerance']}", "Mindermenge-Toleranz (Einheiten)", "darüber: Klärung mit der Filiale, Zahlung zur gelieferten Menge."),
        (f"{dup['fuzzy_window_days']}&nbsp;Tage", "Doppelrechnung (Verdacht)", "gleicher Lieferant, gleicher Bruttobetrag, andere Rechnungsnummer."),
        (f"{hold['skonto_risk_days']}&nbsp;Tage", "Skonto-Risiko-Frist", "Wiedervorlage gilt als dringend, wenn die Frist so nah ist."),
    ]
    body = "".join(
        f'<tr><th scope="row" class="tnum">{v}</th><td>{_esc(name)}</td><td class="spec__note">{_esc(note)}</td></tr>'
        for v, name, note in rows
    )
    return f'<div class="table-scroll spec-scroll"><table class="spec-sheet"><tbody>{body}</tbody></table></div>'


NAV_META_MAX_LEN = 40


def _hero_figure(extrap: dict, today: str, extraction_label: str) -> tuple[str, str, str]:
    """Big number = the estimated handling-time reduction, computed from
    kpi.extrapolate's own before/after hours (never hardcoded). Mirrors
    triage/report.py's _hero_figure shape, with the 'geschätzt' marker
    made into a link to #annahmen right at the number (critique P2 - one
    step further than piece #1's plain-text label)."""
    before = float(extrap["hours_before_per_month"])
    after = float(extrap["hours_after_per_month"])
    pct = round((before - after) / before * 100) if before else 0
    figure_html = (
        f'<p class="figure tnum reveal" style="--i:0" aria-label="minus {pct} Prozent">'
        f'<span class="figure__sign">&minus;</span>'
        f'<span data-count="{pct}">{pct}</span>'
        f'<span class="figure__unit">&nbsp;%</span></p>'
    )
    headline = "weniger Bearbeitungszeit in der Rechnungsprüfung, ohne Zahlungsrisiko."
    label = (f'<a href="#annahmen">geschätzt</a> &middot; Stichtag {_esc(fmt_de_date(today))} '
              f'&middot; Extraktion: {_esc(extraction_label)}')
    return figure_html, headline, label


def _nav_extraction_label(extraction_mode: str) -> str:
    return "LLM" if extraction_mode == "llm" else "Heuristik"


REPORT_CSS = """
/* Hallmark · genre: modern-minimal · macrostructure: Stat-Led · theme: Cobalt · enrichment: none · nav: N9 · footer: Ft2 · tone: technical · anchor hue: 256 · audience: recruiter · use: understand the run in 60 s
 * · shared system with Mail Triage report (user-directed - sibling portfolio pieces, diversification rule suspended per SKILL.md §2 design.md precedent)
 * · contrast: pass (40-41) · slop: pass (42-45) · honest: pass (46) · chrome: pass (47) · tokens: pass (48) · responsive: pass (49) · icons: pass (30) · mobile: pass (34, 49, 50-57) */
/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V3 (Variety intentionally suspended - locked to piece #1's system by user directive, not a quality gap) */

  html, body { overflow-x: clip; }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; scroll-padding-top: calc(var(--banner-height) + var(--space-md)); scrollbar-color: var(--color-rule-strong) var(--color-paper); }
  body {
    margin: 0; background: var(--color-paper); color: var(--color-ink-2);
    font-family: var(--font-body); font-weight: 400; font-size: var(--text-base); line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: var(--color-accent-soft); color: var(--color-ink); }
  :focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; border-radius: 2px; }
  a { color: var(--color-ink); text-decoration: underline; text-decoration-color: var(--color-rule-strong); text-decoration-thickness: 1px; text-underline-offset: 3px;
      transition: text-decoration-color var(--dur-micro) var(--ease-out); }
  a:hover { text-decoration-color: var(--color-accent); }
  h1, h2, h3 { margin: 0; font-family: var(--font-display); font-weight: 600; color: var(--color-ink); letter-spacing: -0.025em; text-wrap: balance; overflow-wrap: anywhere; min-width: 0; }
  h1 { font-size: var(--text-display-s); line-height: 1.08; }
  h2 { font-size: var(--text-xl); line-height: 1.15; }
  h3 { font-size: var(--text-md); line-height: 1.25; }
  h4 { font-family: var(--font-display); font-weight: 600; color: var(--color-ink); font-size: var(--text-base); margin: 0; }
  p { margin: 0; }
  .wrap { max-width: 72rem; margin: 0 auto; padding: 0 var(--space-lg); }
  .mono { font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: 0.06em; text-transform: uppercase; font-weight: 500; }
  .tnum { font-variant-numeric: tabular-nums; }
  .sub { color: var(--color-muted); font-size: var(--text-sm); margin-top: var(--space-3xs); display: block; }
  .note { color: var(--color-muted); font-size: var(--text-sm); max-width: 62ch; }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  code { font-family: var(--font-mono); font-size: var(--text-xs); }

  /* invoice-report-only token extension (kept out of the shared, locked
     tokens.css): a distinct warm urgency signal for a Skonto deadline that
     is today/tomorrow, deliberately apart from --color-status-quarantine
     (reserved for BLOCKED/critical) so "act now for a discount" never
     reads as "something is blocked". */
  :root {
    --color-urgent: oklch(55% 0.21 38);
    --color-urgent-ink: oklch(99% 0.004 250);
  }

  /* N9 · edge-aligned nav, hairline, one sticky element on the page */
  .nav { position: sticky; top: 0; z-index: var(--z-sticky-nav); height: var(--banner-height); background: var(--color-paper-glass); backdrop-filter: blur(8px); border-bottom: 1px solid var(--color-rule); }
  .nav .wrap { height: 100%; display: flex; align-items: center; justify-content: space-between; gap: var(--space-lg); }
  .nav__left { display: flex; align-items: center; gap: var(--space-lg); min-width: 0; }
  .wordmark { font-family: var(--font-display); font-weight: 600; font-size: var(--text-base); letter-spacing: -0.02em; color: var(--color-ink); text-decoration: none; white-space: nowrap; line-height: 1; }
  .nav__meta { color: var(--color-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1; text-transform: none; letter-spacing: 0.02em; }
  .nav__links { list-style: none; margin: 0; padding: 0; display: flex; gap: var(--space-md); }
  .nav__links a { color: var(--color-ink-2); text-decoration: none; white-space: nowrap; line-height: 1; padding: var(--space-xs) 0; position: relative; }
  .nav__links a::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 2px; background: var(--color-accent); transform: scaleX(0); transform-origin: left; transition: transform var(--dur-short) var(--ease-out); }
  .nav__links a:hover::after, .nav__links a:focus-visible::after { transform: scaleX(1); }

  /* H4 · stat-led hero: figure left, words right, data-viz below */
  .hero { padding: var(--space-xl) 0 var(--space-3xl); }
  .hero__grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr); gap: var(--space-xl) var(--space-2xl); align-items: end; }
  .figure__label { color: var(--color-muted); margin: 0 0 var(--space-sm); }
  .figure__label a { color: var(--color-muted); text-decoration-color: var(--color-rule-strong); }
  .figure__label a:hover { color: var(--color-accent); }
  .figure { font-family: var(--font-display); font-weight: 500; font-size: var(--text-figure); line-height: 0.85; letter-spacing: -0.05em; color: var(--color-ink); margin: 0 0 var(--space-md) -0.04em; }
  .figure__sign { font-weight: 500; }
  .figure__unit { font-size: 0.45em; font-weight: 500; letter-spacing: -0.02em; margin-left: 0.05em; }
  .hero__lede { font-size: var(--text-md); line-height: 1.45; color: var(--color-ink-2); max-width: 48ch; }
  .hero__lede strong { color: var(--color-ink); font-weight: 600; }
  .chip { display: inline-flex; align-items: center; gap: var(--space-xs); padding: 0 var(--space-md); height: 2.5rem; border: 1px solid var(--color-rule-strong); border-radius: var(--radius-control);
          background: var(--color-paper); color: var(--color-ink); font-size: var(--text-sm); font-weight: 500; text-decoration: none; white-space: nowrap; line-height: 1; cursor: pointer;
          transition: border-color var(--dur-micro) var(--ease-out), background-color var(--dur-micro) var(--ease-out), transform var(--dur-micro) var(--ease-out); }
  .chip:hover { border-color: var(--color-accent); }
  .chip:active { transform: translateY(1px); }
  .chip--cta { margin-top: var(--space-lg); color: var(--color-accent); border-color: var(--color-accent); }
  .chip--cta:hover { background: var(--color-accent); color: var(--color-accent-ink); }
  .chip--cta::after { content: "\\2192"; }

  .flow { display: flex; gap: 2px; height: 40px; margin-top: var(--space-2xl); border-radius: var(--radius-control); overflow: hidden; }
  .seg { display: grid; place-items: center; min-width: 2rem; font-family: var(--font-display); font-weight: 600; font-size: var(--text-lg); position: relative; }
  .seg-freigegeben { background: var(--color-status-good); color: var(--color-ink); }
  .seg-wiedervorlage { background: var(--color-status-draft); color: var(--color-ink); }
  .seg-klaerung { background: var(--color-status-human); color: var(--color-ink); }
  .seg-gestoppt { background: var(--color-status-quarantine); color: var(--color-accent-ink); }
  .legend { list-style: none; margin: var(--space-sm) 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-xs) var(--space-lg); font-size: var(--text-sm); color: var(--color-muted); }
  .legend li { display: flex; align-items: center; gap: var(--space-xs); }
  .legend b { color: var(--color-ink); font-weight: 600; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .dot-freigegeben { background: var(--color-status-good); } .dot-wiedervorlage { background: var(--color-status-draft); }
  .dot-klaerung { background: var(--color-status-human); } .dot-gestoppt { background: var(--color-status-quarantine); }

  /* one orchestrated entrance, hero only, JS-gated so no-JS renders fully */
  .js .hero .reveal { opacity: 0; transform: translateY(8px); animation: reveal var(--dur-long) var(--ease-out) forwards; animation-delay: calc(var(--i, 0) * 70ms); }
  @keyframes reveal { to { opacity: 1; transform: none; } }

  /* T4 · numbered stat strip */
  .stats { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-top: 1px solid var(--color-rule); border-bottom: 1px solid var(--color-rule); }
  .stats li { padding: var(--space-lg) var(--space-lg) var(--space-lg) 0; display: grid; gap: var(--space-2xs); }
  .stats li + li { padding-left: var(--space-lg); border-left: 1px solid var(--color-rule); }
  .stat__value { font-family: var(--font-display); font-weight: 600; font-size: var(--text-2xl); line-height: 1; letter-spacing: -0.03em; color: var(--color-ink); }
  .stat__label { font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: 0.06em; text-transform: uppercase; font-weight: 500; color: var(--color-ink-2); margin-top: var(--space-xs); }
  .stat__note { color: var(--color-muted); font-size: var(--text-sm); }

  /* sections */
  section.block { padding: var(--space-3xl) 0 0; }
  .block__head { display: grid; grid-template-columns: 1fr; gap: var(--space-xs); max-width: 62ch; }
  .block__head p { color: var(--color-ink-2); }
  .two-col { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--space-2xl); margin-top: var(--space-xl); }
  .two-col h3 { margin-bottom: var(--space-md); }
  .three-col { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-2xl); margin-top: var(--space-xl); }
  .three-col h3 { margin-bottom: var(--space-md); }

  .bars { display: grid; gap: var(--space-sm); }
  .bar-row { display: grid; grid-template-columns: 11rem minmax(0, 1fr) 7.5rem; align-items: center; gap: var(--space-md); }
  .bar-label { font-size: var(--text-sm); color: var(--color-ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bar-track { height: 22px; box-shadow: inset 1px 0 0 var(--color-rule-strong); }
  .bar-fill { height: 100%; width: var(--w); border-radius: 0 3px 3px 0; transition: filter var(--dur-micro) var(--ease-out); }
  .bar-fill:hover { filter: brightness(0.94); }
  .fill-neutral { background: var(--color-neutral-fill); }
  .fill-accent { background: var(--color-accent); }
  .fill-soft { background: var(--color-accent-soft); }
  .bar-value { font-family: var(--font-display); font-weight: 600; color: var(--color-ink); white-space: nowrap; line-height: 1.2; }
  .bars + .note { margin-top: var(--space-lg); }

  /* threats / cases - shared vocabulary for #gestoppt and #arbeitsliste */
  .threats { list-style: none; margin: var(--space-xl) 0 0; padding: 0; border-top: 1px solid var(--color-rule); }
  .threat, .case { padding: var(--space-md) 0; border-bottom: 1px solid var(--color-rule); display: grid; gap: var(--space-2xs); }
  .role-group .threats { margin-top: var(--space-sm); }
  .threat-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-2xs) var(--space-md); }
  .mail-id { color: var(--color-muted); }
  .threat-subject { font-weight: 600; color: var(--color-ink); }
  .threat-from { color: var(--color-muted); font-size: var(--text-sm); }
  .chips { list-style: none; margin: var(--space-xs) 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-xs); }
  .chips li { display: inline-flex; align-items: baseline; gap: var(--space-xs); border: 1px solid var(--color-rule); border-radius: var(--radius-control); padding: var(--space-2xs) var(--space-sm); font-size: var(--text-sm); line-height: 1.4; color: var(--color-ink-2); max-width: 100%; }
  .chips code { color: var(--color-status-quarantine); font-weight: 500; letter-spacing: 0.04em; white-space: nowrap; flex: none; }
  ul.reasons { margin: var(--space-2xs) 0 0; padding-left: var(--space-md); color: var(--color-ink-2); font-size: var(--text-sm); }
  ul.reasons li + li { margin-top: var(--space-3xs); }
  .deadline { color: var(--color-ink); font-weight: 600; }
  .deadline--today, .deadline--tomorrow { display: inline-block; font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: 0.02em; padding: 0.15em 0.55em; border-radius: var(--radius-control); }
  .deadline--today { background: var(--color-urgent); color: var(--color-urgent-ink); }
  .deadline--tomorrow { border: 1px solid var(--color-urgent); color: var(--color-urgent); background: none; }
  details { margin-top: var(--space-xs); }
  summary { cursor: pointer; font-size: var(--text-sm); color: var(--color-accent); font-weight: 500; }
  pre.draft { white-space: pre-wrap; font-family: var(--font-body); font-size: var(--text-sm); line-height: 1.5; color: var(--color-ink-2); background: var(--color-paper-2); border: 1px solid var(--color-rule); border-radius: var(--radius-control); padding: var(--space-sm) var(--space-md); margin: var(--space-xs) 0 0; }

  /* #arbeitsliste severity tiers */
  .severity-tier { margin-top: var(--space-2xl); padding-top: var(--space-lg); border-top: 1px solid var(--color-rule); }
  .severity-tier:first-child { margin-top: var(--space-xl); }
  .tier-heading { display: flex; align-items: center; gap: var(--space-sm); }
  .tier-heading .dot { width: 11px; height: 11px; }
  .role-group { margin-top: var(--space-lg); }
  .role-group h4 { color: var(--color-ink-2); font-size: var(--text-sm); font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 500; }

  /* the one graphite band · F4 step sequence */
  .band { margin-top: var(--space-3xl); padding: var(--space-3xl) 0; background: var(--color-graphite); color: var(--color-graphite-ink); }
  .band h2, .band h3 { color: var(--color-graphite-ink); }
  .band .block__head p { color: var(--color-graphite-muted); }
  .steps { list-style: none; margin: var(--space-xl) 0 0; padding: 0; border-top: 1px solid var(--color-graphite-rule); }
  .step { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: var(--space-md) var(--space-2xl); padding: var(--space-lg) 0; border-bottom: 1px solid var(--color-graphite-rule); }
  .stage { display: block; font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: 0.06em; color: var(--color-accent-on-dark); margin-bottom: var(--space-xs); }
  .step p { color: var(--color-graphite-muted); max-width: 52ch; }
  .step p em { font-style: normal; color: var(--color-graphite-ink); font-weight: 500; }
  .band .table-scroll { border-top-color: var(--color-graphite-rule); }
  .band .spec-sheet th, .band .spec-sheet td { border-bottom: 1px solid var(--color-graphite-rule); color: var(--color-graphite-ink); }
  .band .spec-sheet th { color: var(--color-graphite-ink); }
  .band .spec-sheet .spec__note { color: var(--color-graphite-muted); }

  /* #rechnungen table + CSS-only filter (radios in normal flow, zero size) */
  .filter-input { display: block; width: 0; height: 0; margin: 0; padding: 0; border: 0; opacity: 0; appearance: none; }
  .filters { display: flex; flex-wrap: wrap; gap: var(--space-xs); margin-top: var(--space-xl); }
  .filters .chip { height: 2.25rem; color: var(--color-ink-2); }
  .filters .chip b { color: var(--color-ink); }
  #f-all:checked ~ .filters label[for="f-all"], #f-freigegeben:checked ~ .filters label[for="f-freigegeben"], #f-wiedervorlage:checked ~ .filters label[for="f-wiedervorlage"], #f-klaerung:checked ~ .filters label[for="f-klaerung"], #f-gestoppt:checked ~ .filters label[for="f-gestoppt"] { background: var(--color-ink); border-color: var(--color-ink); color: var(--color-paper); }
  #f-all:checked ~ .filters label[for="f-all"] b, #f-freigegeben:checked ~ .filters label[for="f-freigegeben"] b, #f-wiedervorlage:checked ~ .filters label[for="f-wiedervorlage"] b, #f-klaerung:checked ~ .filters label[for="f-klaerung"] b, #f-gestoppt:checked ~ .filters label[for="f-gestoppt"] b { color: var(--color-paper); }
  #f-all:focus-visible ~ .filters label[for="f-all"], #f-freigegeben:focus-visible ~ .filters label[for="f-freigegeben"], #f-wiedervorlage:focus-visible ~ .filters label[for="f-wiedervorlage"], #f-klaerung:focus-visible ~ .filters label[for="f-klaerung"], #f-gestoppt:focus-visible ~ .filters label[for="f-gestoppt"] { outline: 2px solid var(--color-focus); outline-offset: 2px; }
  #f-freigegeben:checked ~ .table-scroll tr:not(.fam-freigegeben) { display: none; }
  #f-wiedervorlage:checked ~ .table-scroll tr:not(.fam-wiedervorlage) { display: none; }
  #f-klaerung:checked ~ .table-scroll tr:not(.fam-klaerung) { display: none; }
  #f-gestoppt:checked ~ .table-scroll tr:not(.fam-gestoppt) { display: none; }
  .table-scroll { margin-top: var(--space-md); overflow-x: auto; border-top: 1px solid var(--color-rule); }
  .table-scroll.spec-scroll { margin-top: var(--space-xl); }
  table { border-collapse: collapse; width: 100%; min-width: 52rem; }
  thead th { text-align: left; font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: 0.06em; text-transform: uppercase; font-weight: 500; color: var(--color-muted); padding: var(--space-sm) var(--space-md) var(--space-sm) 0; border-bottom: 1px solid var(--color-rule); }
  tbody td { padding: var(--space-sm) var(--space-md) var(--space-sm) 0; border-bottom: 1px solid var(--color-rule); vertical-align: top; font-size: var(--text-sm); }
  tbody tr:hover td { background: var(--color-paper-2); }
  .col-file { width: 16%; }
  .col-why { width: 30%; max-width: 0; color: var(--color-ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  td.nowrap, th.nowrap { white-space: nowrap; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .badge { display: inline-flex; align-items: center; gap: var(--space-xs); font-weight: 600; color: var(--color-ink); white-space: nowrap; }

  /* F3 · spec sheet for the assumptions */
  .spec-sheet { width: 100%; border-collapse: collapse; }
  .spec-sheet th, .spec-sheet td { padding: var(--space-sm) var(--space-md) var(--space-sm) 0; border-bottom: 1px solid var(--color-rule); text-align: left; vertical-align: top; font-size: var(--text-sm); }
  .spec-sheet th { font-family: var(--font-display); font-weight: 600; color: var(--color-ink); white-space: nowrap; width: 7rem; font-size: var(--text-base); }
  .spec-sheet td { color: var(--color-ink); }
  .spec-sheet .spec__note { color: var(--color-muted); }
  .spec-sheet + .note { margin-top: var(--space-md); }

  .closing { margin-top: var(--space-3xl); max-width: 62ch; }
  .closing p { font-size: var(--text-md); line-height: 1.45; color: var(--color-ink); }
  .closing p + p { margin-top: var(--space-sm); font-size: var(--text-base); color: var(--color-ink-2); }

  /* Ft2 · single line */
  .foot-line { margin-top: var(--space-3xl); padding: var(--space-lg) 0 var(--space-2xl); border-top: 1px solid var(--color-rule); color: var(--color-muted); font-size: var(--text-sm); }
  .foot-line .wrap { display: flex; flex-wrap: wrap; gap: var(--space-2xs) var(--space-md); }
  .foot-line span + span::before { content: "\\00b7"; margin-right: var(--space-md); }

  @media (prefers-reduced-motion: reduce) {
    .js .hero .reveal { animation: reveal 150ms linear forwards; transform: none; }
    * { transition-duration: 1ms !important; }
    html { scroll-behavior: auto; }
  }

  @media (max-width: 48rem) {
    .nav__links, .nav__meta { display: none; }
    .hero { padding: var(--space-xl) 0 var(--space-2xl); }
    .hero__grid { grid-template-columns: minmax(0, 1fr); gap: var(--space-lg); }
    .figure { font-size: clamp(5rem, 22vw, 7rem); }
    .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .stats li { padding: var(--space-md) var(--space-md) var(--space-md) 0; }
    .stats li + li { padding-left: var(--space-md); }
    .stats li:nth-child(3) { border-left: 0; padding-left: 0; border-top: 1px solid var(--color-rule); }
    .stats li:nth-child(4) { border-top: 1px solid var(--color-rule); }
    section.block { padding-top: var(--space-2xl); }
    .two-col, .three-col { grid-template-columns: minmax(0, 1fr); gap: var(--space-xl); }
    .bar-row { grid-template-columns: minmax(0, 1fr); gap: var(--space-2xs); }
    .step { grid-template-columns: minmax(0, 1fr); gap: var(--space-xs); }
    .band { padding: var(--space-2xl) 0; }
    .flow { height: 36px; }
    .spec-sheet th { width: auto; }
  }
  @media print {
    .nav, .filters { display: none; }
    .table-scroll { overflow: visible; } table { min-width: 0; }
    .band { print-color-adjust: exact; }
    details { display: block; } details > summary { display: none; } details > pre, details > ul { display: block; }
  }
"""

REPORT_JS = """
(function () {
  document.documentElement.classList.add('js');
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  document.addEventListener('click', function (e) {
    var link = e.target.closest('a[href^="#"]');
    if (!link) { return; }
    var href = link.getAttribute('href');
    if (!href || href.length < 2) { return; }
    var target = document.getElementById(href.slice(1));
    if (!target) { return; }
    try {
      e.preventDefault();
      target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
      history.replaceState(null, '', href);
    } catch (err) {}
  });

  var fig = document.querySelector('[data-count]');
  if (!fig || reduce) { return; }
  var target = parseInt(fig.getAttribute('data-count'), 10);
  if (!(target > 0)) { return; }
  var start = null, dur = 500;
  fig.textContent = '0';
  function tick(t) {
    if (start === null) { start = t; }
    var p = Math.min(1, (t - start) / dur);
    var eased = 1 - Math.pow(1 - p, 3);
    fig.textContent = String(Math.round(eased * target));
    if (p < 1) { window.requestAnimationFrame(tick); }
  }
  window.requestAnimationFrame(tick);
})();
"""


def write_report_html(
    results: list[MatchResult], batch: dict, extrap: dict, skonto_year: str,
    rules_cfg: dict, today: str, extraction_mode: str, out_dir: Path,
) -> Path:
    generated_at = datetime.now().strftime("%d.%m.%Y, %H:%M")
    tokens_css = TOKENS_PATH.read_text(encoding="utf-8") if TOKENS_PATH.is_file() else _FALLBACK_TOKENS
    total = batch["total_invoices"]
    extraction_label = _nav_extraction_label(extraction_mode)

    # Critique P1 fix (same pattern as triage/report.py's step2): the
    # extraction-step copy in the pipeline band must describe what THIS RUN
    # actually did, never a fixed aspirational claim.
    if extraction_mode == "llm":
        step1 = (
            "E-Rechnungen (XML) werden direkt eingelesen, keine Extraktion nötig. Freitext-Rechnungen: "
            f"<em>dieser Lauf nutzte LLM-Extraktion</em>, mit automatischem Rückfall auf die Regel-Heuristik "
            "bei jedem Fehler (Netzwerk, ungültiges JSON, fehlende Konfiguration)."
        )
    else:
        step1 = (
            "E-Rechnungen (XML) werden direkt eingelesen, keine Extraktion nötig. Freitext-Rechnungen: im "
            "Zielbild wahlweise LLM-Extraktion. <em>Dieser Lauf nutzte die regelbasierte Heuristik (Regex, "
            "offline, kein Netzwerkzugriff).</em>"
        )

    gestoppt_n = batch["blocked"]
    figure_html, figure_headline, figure_label = _hero_figure(extrap, today, extraction_label)
    filter_inputs, filter_labels = _filter_controls(results)

    page = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Invoice Match &middot; Report</title>
<link rel="icon" href="{FAVICON}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS_HREF}">
<style>
{tokens_css}
{REPORT_CSS}</style>
</head>
<body>
<header class="nav" role="banner">
  <div class="wrap">
    <div class="nav__left">
      <a class="wordmark" href="#top">Invoice Match</a>
      <p class="nav__meta mono">Stichtag {_esc(fmt_de_date(today))} &middot; Extraktion: {_esc(extraction_label)}</p>
    </div>
    <nav aria-label="Abschnitte">
      <ul class="nav__links mono">
        <li><a href="#nutzen">Nutzen</a></li>
        <li><a href="#gestoppt">Gestoppt</a></li>
        <li><a href="#arbeitsliste">Arbeitsliste</a></li>
        <li><a href="#funktionsweise">Funktionsweise</a></li>
        <li><a href="#rechnungen">Rechnungen</a></li>
        <li><a href="#annahmen">Annahmen</a></li>
        <li><a href="../docs/index.html">README</a></li>
      </ul>
    </nav>
  </div>
</header>

<main id="top">
  <section class="hero">
    <div class="wrap">
      <div class="hero__grid">
        <div class="hero__lead">
          <p class="figure__label mono reveal" style="--i:0">{figure_label}</p>
          {figure_html}
          <h1 class="reveal" style="--i:1">{_esc(figure_headline)}</h1>
        </div>
        <div class="hero__aside reveal" style="--i:2">
          <p class="hero__lede">
            <strong>{total} Rechnungen</strong> für Brandt Haus &amp; Garten GmbH geprüft, gegen Bestellung und
            Wareneingang abgeglichen und entschieden. <strong>{batch['approved']} automatisch freigegeben</strong>,
            <strong>{gestoppt_n} Zahlungen gestoppt</strong>, <strong>{_esc(fmt_eur(batch['money_at_risk_total_eur']))}
            Risiko abgefangen</strong>.
          </p>
          <a class="chip chip--cta" href="#rechnungen">Alle {total} Rechnungen ansehen</a>
        </div>
      </div>
      <div class="reveal" style="--i:3">{_flow_bar(results)}</div>
    </div>
  </section>

  <div class="wrap">
    {_stat_strip(batch, extrap, skonto_year)}
  </div>

  <section class="block" id="nutzen">
    <div class="wrap">
      <div class="block__head">
        <h2>Was die Automatisierung bringt</h2>
        <p>Bearbeitungszeit, Arbeitsschritte und abgefangenes Risiko für genau diesen Rechnungslauf. Alle
          hochgerechneten Werte sind Schätzungen aus den Annahmen in <code>rules.toml</code>.</p>
      </div>
      <div class="three-col">
        <div>
          <h3>Stunden pro Monat</h3>
          {_before_after_hours(extrap)}
        </div>
        <div>
          <h3>Schritte je unauffälliger Rechnung</h3>
          {_before_after_steps()}
        </div>
        <div>
          <h3>Risiko abgefangen, nach Regel</h3>
          {_money_at_risk_bars(batch)}
        </div>
      </div>
    </div>
  </section>

  <section class="block" id="gestoppt">
    <div class="wrap">
      <div class="block__head">
        <h2>Gestoppt: {gestoppt_n} Zahlungen blockiert</h2>
        <p>Kritische Befunde blockieren die Zahlung - egal, wie unauffällig die Rechnung sonst aussieht.</p>
      </div>
      {_gestoppt_section(results)}
    </div>
  </section>

  <section class="block" id="arbeitsliste">
    <div class="wrap">
      <div class="block__head">
        <h2>Arbeitsliste</h2>
        <p>Nach Dringlichkeit sortiert (Gestoppt vor Klärung vor Wiedervorlage), innerhalb dessen nach
          zuständiger Rolle. Jeder Fall zeigt Grund, Empfehlung, Zahlbetrag und - bei Lieferantenfällen - einen
          fertigen deutschen Entwurf.</p>
      </div>
      {_worklist_section(results, today)}
    </div>
  </section>

  <section class="band" id="funktionsweise">
    <div class="wrap">
      <div class="block__head">
        <h2>So funktioniert es</h2>
        <p>Fünf Schritte je Rechnung. Die KI liest nur unstrukturierten Text; die Zahlungsentscheidung trifft
          ausschließlich das Regelwerk unten.</p>
      </div>
      <ol class="steps">
        <li class="step"><div><span class="stage">1.0</span><h3>Rechnungseingang</h3></div><p>{step1}</p></li>
        <li class="step"><div><span class="stage">2.0</span><h3>Plausibilitätsprüfung</h3></div><p>Positionssumme gegen Nettobetrag, Netto plus USt gegen Bruttobetrag - jede Rechnung, unabhängig vom Extraktionsweg. Fängt fehlerhafte Extraktion und KI-Halluzination ab, bevor irgendetwas gegen eine Bestellung geprüft wird.</p></li>
        <li class="step"><div><span class="stage">3.0</span><h3>Abgleich &amp; Regelwerk</h3></div><p>Dreiwege-Abgleich (Rechnung / Bestellung / Wareneingang), Duplikatsprüfung gegen die Zahlungshistorie, IBAN-Abgleich mit den Lieferantenstammdaten. Deterministisch, konfiguriert in <code>rules.toml</code> - niemals die KI.</p></li>
        <li class="step"><div><span class="stage">4.0</span><h3>Routing &amp; Entwurf</h3></div><p>Jede Ausnahme geht an die zuständige Rolle mit Handlungsempfehlung und Zahlbetrag. Für Lieferantenfälle liegt ein fertiger deutscher Entwurf bei - textbausteinbasiert, nie durch ein Sprachmodell erzeugt.</p></li>
        <li class="step"><div><span class="stage">5.0</span><h3>Bericht &amp; Prüfliste</h3></div><p>Dieses Dashboard, <code>worklist.csv</code> für ein Ticketsystem und <code>results.json</code> als vollständiges, maschinenlesbares Ergebnis - für jeden Lauf neu erzeugt.</p></li>
      </ol>
      <div class="block__head" style="margin-top:var(--space-2xl)">
        <h3>Regelwerk in diesem Lauf</h3>
      </div>
      {_rules_de_band(rules_cfg)}
    </div>
  </section>

  <section class="block" id="rechnungen">
    <div class="wrap">
      <div class="block__head">
        <h2>Alle {total} Rechnungen</h2>
        <p>Lieferant, Betrag, Entscheidung und Begründung je Rechnung.</p>
      </div>
      {filter_inputs}
      <div class="filters" role="group" aria-label="Nach Entscheidung filtern">{filter_labels}</div>
      <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Datei</th><th>Lieferant</th><th>Betrag</th><th>Entscheidung</th><th>Route</th><th>Begründung</th></tr>
        </thead>
        <tbody>{_all_invoices_table(results)}
        </tbody>
      </table>
      </div>
    </div>
  </section>

  <section class="block" id="annahmen">
    <div class="wrap">
      <div class="block__head">
        <h2>Annahmen hinter den Schätzungen</h2>
        <p>Aus <code>rules.toml [kpi]</code>. Schätzungen, die eine Messung im echten Betrieb vorbereiten,
          nicht ersetzen - siehe README, Abschnitt „Value (Nutzen)“.</p>
      </div>
      <div class="table-scroll spec-scroll"><table class="spec-sheet"><tbody>
        <tr><th scope="row" class="tnum">{rules_cfg['kpi']['invoices_per_month']}</th><td>Rechnungen pro Monat</td><td class="spec__note">Annahme für die Hochrechnung, nicht gemessen.</td></tr>
        <tr><th scope="row" class="tnum">{rules_cfg['kpi']['manual_minutes_per_invoice']}&nbsp;min</th><td>Anfängliche Bearbeitung je Rechnung heute</td><td class="spec__note">Für jede Rechnung, da vorab nicht bekannt ist, welche unauffällig ist.</td></tr>
        <tr><th scope="row" class="tnum">{rules_cfg['kpi']['exception_minutes_manual']}&nbsp;min</th><td>Volle manuelle Prüfung heute</td><td class="spec__note">Für die rund {fmt_de_number(extrap['human_share_pct'], 1)}&nbsp;% der Rechnungen, die heute weitere Prüfung brauchen.</td></tr>
        <tr><th scope="row" class="tnum">{rules_cfg['kpi']['exception_minutes_assisted']}&nbsp;min</th><td>Prüfung nach Automatisierung</td><td class="spec__note">Mit Vorprüfung und fertigem Entwurf, nur noch für den Klärungs-Anteil.</td></tr>
        <tr><th scope="row" class="tnum">{fmt_eur(rules_cfg['kpi']['hourly_cost_eur'])}</th><td>Kostensatz je Stunde</td><td class="spec__note">Vollkosten Kreditorenbuchhaltung.</td></tr>
        <tr><th scope="row" class="tnum">140&nbsp;h</th><td>Produktive Stunden je Vollzeitstelle/Monat</td><td class="spec__note">Für die FTE-Umrechnung.</td></tr>
        <tr><th scope="row" class="tnum">{_de(float(rules_cfg['kpi']['skonto_eligible_share_lost_today'])*100, 0)}&nbsp;%</th><td>Verpasstes Skonto heute</td><td class="spec__note">Anteil skontofähiger Rechnungen, die die Frist heute reißen (Schätzung).</td></tr>
      </tbody></table></div>
      <p class="note">Die Stichprobe enthält bewusst einen Fall je Regel - die gemessene
        Dunkelverarbeitungsquote von {_de(batch['touchless_rate_pct'], 1)}&nbsp;% ist daher eine Untergrenze
        für den echten Betrieb, nicht der Zielwert. Die Seite ist druckoptimiert.</p>
    </div>
  </section>

  <div class="wrap">
    <div class="closing">
      <p>Ergebnis dieses Laufs: {gestoppt_n} Zahlungen gestoppt, {batch['approved']} von {total} Rechnungen
      automatisch freigegeben, {_esc(fmt_eur(batch['money_at_risk_total_eur']))} Risiko abgefangen. Jede
      Entscheidung ist in <code>rules.toml</code> nachvollziehbar.</p>
      <p>Nächster Schritt: die Arbeitsliste oben abarbeiten, dann <code>python run_match.py</code> erneut
      ausführen, sobald offene Wareneingänge gebucht sind.</p>
    </div>
  </div>
</main>

<footer class="foot-line">
  <div class="wrap">
    <span>Brandt Haus &amp; Garten GmbH</span><span>Rechnungsprüfung</span><span>Lauf vom {_esc(generated_at)}</span><span>erzeugt von run_match.py</span>
  </div>
</footer>
<script>{REPORT_JS}</script>
</body>
</html>
"""
    path = out_dir / "report.html"
    path.write_text(page, encoding="utf-8")
    return path


def write_all(
    results: list[MatchResult], rules_cfg: dict, today: str, extraction_mode: str, out_dir: Path,
) -> tuple[dict[str, Path], dict, dict, str]:
    """Convenience entry point used by run_match.py: computes the KPIs once
    and writes every output file from the same numbers."""
    out_dir.mkdir(parents=True, exist_ok=True)
    batch = kpi_mod.batch_kpis(results)
    extrap = kpi_mod.extrapolate(batch, rules_cfg["kpi"])
    skonto_year = kpi_mod.skonto_recovered_per_year(results, rules_cfg["kpi"])

    paths = {
        "results_json": write_results_json(results, batch, extrap, skonto_year, today, extraction_mode, out_dir),
        "worklist_csv": write_worklist_csv(results, out_dir),
        "report_html": write_report_html(results, batch, extrap, skonto_year, rules_cfg, today, extraction_mode, out_dir),
    }
    from .drafts import write_drafts
    write_drafts(results, out_dir)
    return paths, batch, extrap, skonto_year
