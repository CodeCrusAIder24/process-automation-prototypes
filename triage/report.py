"""Console output, results.json, audit.jsonl and the self-contained report.html."""
from __future__ import annotations

import html
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .models import MailResult

POLICY_PATH = Path(__file__).resolve().parent.parent / "policy.toml"


def _load_metrics_assumptions() -> dict:
    with open(POLICY_PATH, "rb") as f:
        return tomllib.load(f)["metrics"]

def print_console_summary(results: list[MailResult], level: int, classifier_mode: str, kpis: dict) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"Mail Triage Agent - Autonomiestufe {level} - Classifier: {classifier_mode}")
    print("-" * 100)
    header = f"{'ID':<5} {'Kategorie (Konf.)':<28} {'Risiko':<8} {'Entscheidung':<18} {'Ziel':<24}"
    print(header)
    print("-" * 100)
    for r in results:
        cat_conf = f"{r.classification.category} ({r.classification.confidence:.2f})"
        line = (
            f"{r.mail.id:<5} {cat_conf:<28} {r.security.risk_level:<8} "
            f"{r.decision.action:<18} {r.decision.target:<24}"
        )
        print(line)
    print("-" * 100)
    print("KPIs (Schätzungen, siehe policy.toml):")
    print(f"  Mails gesamt:              {kpis['total_mails']}")
    print(f"  Automatisch bearbeitet:    {kpis['auto_handled']} ({kpis['auto_handled_pct']}%)")
    print(f"  Mensch erforderlich:       {kpis['human_required']} ({kpis['human_required_pct']}%)")
    print(f"  Bedrohungen blockiert:     {kpis['threats_blocked']}")
    print(f"  Injection-Versuche:        {kpis['injection_attempts_detected']}")
    print(f"  Minuten vorher:            {kpis['minutes_before']}")
    print(f"  Minuten nachher:           {kpis['minutes_after']}")
    print(f"  Minuten gespart:           {kpis['minutes_saved']} ({kpis['saved_pct']}%)")
    print(f"  Hochgerechnet/Monat:       {kpis['monthly_hours_saved']} Stunden gespart "
          f"(bei {kpis['mails_per_day']} Mails/Tag, {kpis['working_days_per_month']} Arbeitstagen)")


def write_results_json(results: list[MailResult], kpis: dict, rampup: list[dict], level: int,
                        classifier_mode: str, out_dir: Path) -> Path:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "autonomy_level": level,
        "classifier_mode": classifier_mode,
        "kpis": kpis,
        "rampup": rampup,
        "mails": [r.to_dict() for r in results],
    }
    path = out_dir / "results.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_audit_jsonl(results: list[MailResult], level: int, classifier_mode: str, out_dir: Path) -> Path:
    path = out_dir / "audit.jsonl"
    lines = []
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        entry = {
            "timestamp": now,
            "mail_id": r.mail.id,
            "autonomy_level": level,
            "classifier": r.classification.classifier,
            "security_findings": [f.to_dict() for f in r.security.findings],
            "risk_level": r.security.risk_level,
            "risk_score": r.security.risk_score,
            "injection_suspected": r.security.injection_suspected,
            "classification": {
                "category": r.classification.category,
                "confidence": r.classification.confidence,
                "requests_nonpublic_info": r.classification.requests_nonpublic_info,
            },
            "decision": {
                "action": r.decision.action,
                "target": r.decision.target,
                "reasons": r.decision.reasons,
                "requires_human": r.decision.requires_human,
            },
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _de(num, digits: int = 1) -> str:
    """German number formatting: 63.5 -> '63,5', 44 -> '44'."""
    if isinstance(num, int) or digits == 0:
        return f"{int(round(num)):,}".replace(",", ".")
    return f"{num:.{digits}f}".replace(".", ",")


TOKENS_PATH = Path(__file__).resolve().parent.parent / "tokens.css"

LEVEL_NAMES = {0: "Beobachten", 1: "Assistieren", 2: "Handeln (risikoarm)", 3: "Erweitert"}

# Decision families for the flow bar and the table filter. Order = left to right.
FLOW_FAMILIES = [
    ("auto", "Automatisch erledigt", {"AUTO_REPLY", "AUTO_ROUTE", "AUTO_ARCHIVE"}),
    ("draft", "Entwurf zur Prüfung", {"DRAFT_FOR_REVIEW"}),
    ("human", "An Mensch eskaliert", {"ESCALATE_HUMAN"}),
    ("quarantine", "Quarantäne", {"QUARANTINE"}),
]
ACTION_FAMILY = {a: fam for fam, _, actions in FLOW_FAMILIES for a in actions}
ACTION_LABELS = {
    "AUTO_REPLY": "Automatisch beantwortet",
    "AUTO_ROUTE": "Automatisch weitergeleitet",
    "AUTO_ARCHIVE": "Automatisch archiviert",
    "DRAFT_FOR_REVIEW": "Entwurf zur Prüfung",
    "ESCALATE_HUMAN": "An Mensch eskaliert",
    "QUARANTINE": "Quarantäne",
}
CATEGORY_LABELS = {
    "faq_question": "FAQ-Frage", "sales_lead": "Vertriebsanfrage", "order_status": "Auftragsstatus",
    "invoice": "Rechnung", "meeting_request": "Terminanfrage", "complaint": "Beschwerde",
    "job_application": "Bewerbung", "gdpr_request": "DSGVO-Anfrage", "newsletter": "Newsletter",
    "auto_reply": "Auto-Antwort", "spam_or_phishing": "Spam / Phishing",
    "internal_info_request": "Anfrage interner Infos", "unclear": "Unklar",
}
RISK_LABELS = {"none": "kein", "low": "niedrig", "medium": "mittel", "high": "hoch"}
MAX_VISIBLE_REASONS = 3

# Inline SVG favicon: the flow bar in miniature, in the token colours.
FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='7' fill='oklch(22%25 0.016 260)'/%3E"
    "%3Crect x='5' y='12' width='11' height='8' rx='1.5' fill='oklch(54%25 0.2 256)'/%3E"
    "%3Crect x='17' y='12' width='4' height='8' rx='1.5' fill='oklch(80%25 0.16 85)'/%3E"
    "%3Crect x='22' y='12' width='5' height='8' rx='1.5' fill='oklch(52%25 0.2 25)'/%3E"
    "%3C/svg%3E"
)

FONTS_HREF = ("https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600"
              "&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap")


def _family_counts(results: list[MailResult]) -> dict[str, int]:
    counts = {fam: 0 for fam, _, _ in FLOW_FAMILIES}
    for r in results:
        counts[ACTION_FAMILY.get(r.decision.action, "human")] += 1
    return counts


def _flow_bar(results: list[MailResult]) -> str:
    total = len(results) or 1
    counts = _family_counts(results)
    segments, legend, spoken = [], [], []
    for fam, label, _ in FLOW_FAMILIES:
        n = counts[fam]
        if n == 0:
            continue
        pct = n / total * 100
        segments.append(
            f'<div class="seg seg-{fam}" style="flex-basis:{pct:.2f}%" '
            f'title="{_esc(label)}: {n} von {total} Mails"><span class="tnum">{n}</span></div>'
        )
        legend.append(f'<li><i class="dot dot-{fam}"></i>{_esc(label)}<b class="tnum">{n}</b></li>')
        spoken.append(f"{n} {label}")
    aria = f"Verteilung der {total} Mails: " + ", ".join(spoken) + "."
    return (
        f'<div class="flow" role="img" aria-label="{_esc(aria)}">' + "".join(segments) + "</div>"
        + '<ul class="legend">' + "".join(legend) + "</ul>"
    )


def _stat_strip(kpis: dict) -> str:
    items = [
        (f"{_de(kpis['monthly_hours_saved'], 0)}&nbsp;h", "pro Monat gespart",
         f"geschätzt, bei {kpis['mails_per_day']} Mails am Tag"),
        (str(kpis["auto_handled"]), "automatisch erledigt", f"{_de(kpis['auto_handled_pct'], 0)}&nbsp;% des Posteingangs"),
        (str(kpis["threats_blocked"]), "Angriffe gestoppt",
         f"davon {kpis['injection_attempts_detected']} Prompt-Injections"),
        (str(kpis["total_mails"]), "Mails geprüft",
         f"{_de(kpis['minutes_before'])} auf {_de(kpis['minutes_after'])}&nbsp;min"),
    ]
    out = []
    for value, label, note in items:
        out.append(f'<li><span class="stat__value tnum">{value}</span>'
                   f'<span class="stat__label">{label}</span><span class="stat__note">{note}</span></li>')
    return '<ol class="stats">' + "".join(out) + "</ol>"


def _before_after(kpis: dict) -> str:
    before, after = kpis["minutes_before"], kpis["minutes_after"]
    after_pct = (after / before * 100) if before else 0
    return f"""
<div class="bars">
  <div class="bar-row">
    <div class="bar-label">Heute, manuell</div>
    <div class="bar-track"><div class="bar-fill fill-neutral" style="--w:100%"></div></div>
    <div class="bar-value tnum">{_de(before)}&nbsp;min</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Mit Triage-Agent</div>
    <div class="bar-track"><div class="bar-fill fill-accent" style="--w:{after_pct:.1f}%"></div></div>
    <div class="bar-value tnum">{_de(after)}&nbsp;min</div>
  </div>
</div>
<p class="note">Geschätzt &asymp; {_de(kpis['monthly_hours_saved'], 0)}&nbsp;Stunden pro Monat bei {kpis['mails_per_day']} Mails am Tag
und {kpis['working_days_per_month']} Arbeitstagen. <a href="#annahmen">Annahmen ansehen</a></p>"""


def _rampup_bars(rampup: list[dict], level: int) -> str:
    max_h = max((r["monthly_hours_saved"] for r in rampup), default=1) or 1
    rows = []
    for r in rampup:
        lv = r["level"]
        is_current = lv == level
        w = r["monthly_hours_saved"] / max_h * 100
        current_cls = " is-current" if is_current else ""
        current_tag = ' <em class="mono">aktuell</em>' if is_current else ""
        fill_cls = "fill-accent" if is_current else "fill-soft"
        rows.append(f"""
  <div class="bar-row{current_cls}">
    <div class="bar-label">Stufe {lv} &middot; {_esc(LEVEL_NAMES[lv])}{current_tag}</div>
    <div class="bar-track"><div class="bar-fill {fill_cls}" style="--w:{w:.1f}%"
      title="Stufe {lv}: {_de(r['monthly_hours_saved'])} h/Monat, {_de(r['auto_handled_pct'])} % automatisch"></div></div>
    <div class="bar-value tnum">{_de(r['monthly_hours_saved'], 0)}&nbsp;h<span class="sub">{_de(r['auto_handled_pct'], 0)}&nbsp;% automatisch</span></div>
  </div>""")
    return '<div class="bars rampup">' + "".join(rows) + "</div>"


def _chips(findings) -> str:
    if not findings:
        return ""
    return '<ul class="chips">' + "".join(
        f'<li><code>{_esc(f.rule_id)}</code>{_esc(f.description)}</li>' for f in findings
    ) + "</ul>"


def _threats(results: list[MailResult]) -> str:
    blocked = [r for r in results if r.decision.action == "QUARANTINE"]
    if not blocked:
        return '<p class="note">In diesem Lauf wurde keine Mail unter Quarantäne gestellt.</p>'
    items = []
    for r in blocked:
        items.append(f"""
  <li class="threat">
    <div class="threat-head">
      <span class="mail-id mono">{_esc(r.mail.id)}</span>
      <span class="threat-subject">{_esc(r.mail.subject)}</span>
      <span class="threat-from">{_esc(r.mail.from_name)} &lt;{_esc(r.mail.from_addr)}&gt;</span>
    </div>
    {_chips(r.security.findings)}
  </li>""")
    return '<ol class="threats">' + "".join(items) + "</ol>"


def _reason_item(reason: str) -> str:
    if "Frist" in reason:
        return f'<li><strong class="deadline">{_esc(reason)}</strong></li>'
    return f"<li>{_esc(reason)}</li>"


def _reasons_html(reasons: list[str]) -> str:
    visible = "".join(_reason_item(x) for x in reasons[:MAX_VISIBLE_REASONS])
    rest = reasons[MAX_VISIBLE_REASONS:]
    out = f'<ul class="reasons">{visible}</ul>'
    if rest:
        more = "".join(_reason_item(x) for x in rest)
        out += (f'<details class="more"><summary>{len(rest)} weitere Gründe</summary>'
                f'<ul class="reasons">{more}</ul></details>')
    return out


def _filter_controls(results: list[MailResult]) -> tuple[str, str]:
    """Radio inputs must be siblings of .table-scroll for the CSS-only filter, so they
    are returned separately from their labels."""
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


def _decisions_table(results: list[MailResult]) -> str:
    rows = []
    for r in results:
        fam = ACTION_FAMILY.get(r.decision.action, "human")
        draft_html = ""
        if r.decision.draft:
            draft_html = (
                "<details><summary>Antwortentwurf anzeigen</summary>"
                f"<pre class='draft'>{_esc(r.decision.draft)}</pre></details>"
            )
        cat = CATEGORY_LABELS.get(r.classification.category, r.classification.category)
        risk = r.security.risk_level
        action_label = ACTION_LABELS.get(r.decision.action, r.decision.action)
        rows.append(f"""
      <tr class="fam-{fam}">
        <td class="col-mail"><span class="mail-id mono">{_esc(r.mail.id)}</span>
          <div class="subject">{_esc(r.mail.subject)}</div>
          <div class="from">{_esc(r.mail.from_name)} &middot; {_esc(r.mail.from_addr)}</div></td>
        <td class="col-cat">{_esc(cat)}<div class="sub tnum">Konfidenz {_de(r.classification.confidence * 100, 0)}&nbsp;%</div></td>
        <td class="col-risk"><i class="dot risk-{_esc(risk)}"></i>{_esc(RISK_LABELS.get(risk, risk))}<div class="sub tnum">Score {r.security.risk_score}&nbsp;/&nbsp;100</div></td>
        <td class="col-decision"><span class="badge"><i class="dot dot-{fam}"></i>{_esc(action_label)}</span>
          <div class="sub">{_esc(r.decision.target)}</div></td>
        <td class="col-why">{_reasons_html(r.decision.reasons)}{draft_html}</td>
      </tr>""")
    return "".join(rows)


def _assumptions_table(m: dict) -> str:
    rows = [
        (m["triage_minutes"], "Manuelle Triage je Mail", "Lesen, Entscheiden, Weiterleiten. Baseline ohne Agent."),
        (m["assisted_triage_minutes"], "Sichtung mit KI-Vorschlag", "Kategorie und Zusammenfassung liegen vor. Auf Stufe 0 gilt weiterhin die volle Triage-Zeit."),
        (m["draft_review_minutes"], "Prüfung eines Antwortentwurfs", "Entwurf aus der öffentlichen Wissensbasis, vom Leak-Guard freigegeben."),
        (m["quarantine_confirm_minutes"], "Bestätigung einer Quarantäne", "Kurzer Blick auf die Regelbefunde."),
        (m["auto_spot_check_minutes"], "Stichprobe je automatischer Weiterleitung", "Nicht jede Mail, sondern eine Stichprobe im Aggregat."),
    ]
    body = "".join(
        f'<tr><th scope="row" class="tnum">{_de(v)}&nbsp;min</th><td>{_esc(name)}</td><td class="spec__note">{_esc(note)}</td></tr>'
        for v, name, note in rows
    )
    return f'<table class="spec-sheet"><tbody>{body}</tbody></table>'


REPORT_CSS = """
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
  p { margin: 0; }
  .wrap { max-width: 72rem; margin: 0 auto; padding: 0 var(--space-lg); }
  .mono { font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: 0.06em; text-transform: uppercase; font-weight: 500; }
  .tnum { font-variant-numeric: tabular-nums; }
  .sub { color: var(--color-muted); font-size: var(--text-sm); margin-top: var(--space-3xs); }
  .note { color: var(--color-muted); font-size: var(--text-sm); max-width: 62ch; }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  code { font-family: var(--font-mono); font-size: var(--text-xs); }

  /* N9 · edge-aligned nav, hairline, one sticky element on the page */
  .nav { position: sticky; top: 0; z-index: var(--z-sticky-nav); height: var(--banner-height); background: var(--color-paper-glass); backdrop-filter: blur(8px); border-bottom: 1px solid var(--color-rule); }
  .nav .wrap { height: 100%; display: flex; align-items: center; justify-content: space-between; gap: var(--space-lg); }
  .nav__left { display: flex; align-items: center; gap: var(--space-lg); min-width: 0; }
  .wordmark { font-family: var(--font-display); font-weight: 600; font-size: var(--text-base); letter-spacing: -0.02em; color: var(--color-ink); text-decoration: none; white-space: nowrap; line-height: 1; }
  .nav__meta { color: var(--color-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1; text-transform: none; letter-spacing: 0.02em; }
  .nav__links { list-style: none; margin: 0; padding: 0; display: flex; gap: var(--space-lg); }
  .nav__links a { color: var(--color-ink-2); text-decoration: none; white-space: nowrap; line-height: 1; padding: var(--space-xs) 0; position: relative; }
  .nav__links a::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 2px; background: var(--color-accent); transform: scaleX(0); transform-origin: left; transition: transform var(--dur-short) var(--ease-out); }
  .nav__links a:hover::after, .nav__links a:focus-visible::after { transform: scaleX(1); }

  /* H4 · stat-led hero: figure left, words right, data-viz below */
  .hero { padding: var(--space-xl) 0 var(--space-3xl); }
  .hero__grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr); gap: var(--space-xl) var(--space-2xl); align-items: end; }
  .figure__label { color: var(--color-muted); margin: 0 0 var(--space-sm); }
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
  .chip:disabled, .chip[aria-disabled="true"], .filter-input:disabled ~ .filters label { opacity: 0.55; cursor: not-allowed; pointer-events: none; }
  .chip--cta { margin-top: var(--space-lg); color: var(--color-accent); border-color: var(--color-accent); }
  .chip--cta:hover { background: var(--color-accent); color: var(--color-accent-ink); }
  .chip--cta::after { content: "\\2192"; }

  .flow { display: flex; gap: 2px; height: 40px; margin-top: var(--space-2xl); border-radius: var(--radius-control); overflow: hidden; }
  .seg { display: grid; place-items: center; min-width: 2rem; font-family: var(--font-display); font-weight: 600; font-size: var(--text-base); }
  .seg-auto { background: var(--color-status-auto); color: var(--color-accent-ink); }
  .seg-draft { background: var(--color-status-draft); color: var(--color-ink); }
  .seg-human { background: var(--color-status-human); color: var(--color-ink); }
  .seg-quarantine { background: var(--color-status-quarantine); color: var(--color-accent-ink); }
  .legend { list-style: none; margin: var(--space-sm) 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-xs) var(--space-lg); font-size: var(--text-sm); color: var(--color-muted); }
  .legend li { display: flex; align-items: center; gap: var(--space-xs); }
  .legend b { color: var(--color-ink); font-weight: 600; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .dot-auto { background: var(--color-status-auto); } .dot-draft { background: var(--color-status-draft); }
  .dot-human { background: var(--color-status-human); } .dot-quarantine { background: var(--color-status-quarantine); }

  /* one orchestrated entrance, hero only, JS-gated so no-JS renders fully */
  .js .hero .reveal { opacity: 0; transform: translateY(8px); animation: reveal var(--dur-long) var(--ease-out) forwards; animation-delay: calc(var(--i, 0) * 70ms); }
  @keyframes reveal { to { opacity: 1; transform: none; } }

  /* T4 · numbered stat strip */
  .stats { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-top: 1px solid var(--color-rule); border-bottom: 1px solid var(--color-rule); counter-reset: stat; }
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

  .bars { display: grid; gap: var(--space-sm); }
  .bar-row { display: grid; grid-template-columns: 11rem minmax(0, 1fr) 7.5rem; align-items: center; gap: var(--space-md); }
  .bar-label { font-size: var(--text-sm); color: var(--color-ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bar-label em { font-style: normal; color: var(--color-accent); margin-left: var(--space-xs); }
  .is-current .bar-label { color: var(--color-ink); font-weight: 600; }
  .bar-track { height: 22px; box-shadow: inset 1px 0 0 var(--color-rule-strong); }
  .bar-fill { height: 100%; width: var(--w); border-radius: 0 3px 3px 0; transition: filter var(--dur-micro) var(--ease-out); }
  .bar-fill:hover { filter: brightness(0.94); }
  .fill-neutral { background: var(--color-neutral-fill); }
  .fill-accent { background: var(--color-accent); }
  .fill-soft { background: var(--color-accent-soft); }
  .bar-value { font-family: var(--font-display); font-weight: 600; color: var(--color-ink); white-space: nowrap; line-height: 1.2; }
  .bar-value .sub { display: block; font-family: var(--font-body); font-weight: 400; }
  .bars + .note { margin-top: var(--space-lg); }

  /* threats */
  .threats { list-style: none; margin: var(--space-xl) 0 0; padding: 0; border-top: 1px solid var(--color-rule); }
  .threat { padding: var(--space-md) 0; border-bottom: 1px solid var(--color-rule); display: grid; gap: var(--space-2xs); }
  .threat-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-2xs) var(--space-md); }
  .mail-id { color: var(--color-muted); }
  .threat-subject { font-weight: 600; color: var(--color-ink); }
  .threat-from { color: var(--color-muted); font-size: var(--text-sm); }
  .chips { list-style: none; margin: var(--space-xs) 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-xs); }
  .chips li { display: inline-flex; align-items: baseline; gap: var(--space-xs); border: 1px solid var(--color-rule); border-radius: var(--radius-control); padding: var(--space-2xs) var(--space-sm); font-size: var(--text-sm); line-height: 1.4; color: var(--color-ink-2); max-width: 100%; }
  .chips code { color: var(--color-status-quarantine); font-weight: 500; letter-spacing: 0.04em; white-space: nowrap; flex: none; }

  /* the one graphite band · F4 step sequence */
  .band { margin-top: var(--space-3xl); padding: var(--space-3xl) 0; background: var(--color-graphite); color: var(--color-graphite-ink); }
  .band h2, .band h3 { color: var(--color-graphite-ink); }
  .band .block__head p { color: var(--color-graphite-muted); }
  .steps { list-style: none; margin: var(--space-xl) 0 0; padding: 0; border-top: 1px solid var(--color-graphite-rule); }
  .step { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: var(--space-md) var(--space-2xl); padding: var(--space-lg) 0; border-bottom: 1px solid var(--color-graphite-rule); }
  .stage { display: block; font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: 0.06em; color: var(--color-accent-on-dark); margin-bottom: var(--space-xs); }
  .step p { color: var(--color-graphite-muted); max-width: 52ch; }
  .step p em { font-style: normal; color: var(--color-graphite-ink); font-weight: 500; }

  /* decisions table + CSS-only filter (radios in normal flow, zero size) */
  .filter-input { display: block; width: 0; height: 0; margin: 0; padding: 0; border: 0; opacity: 0; appearance: none; }
  .filters { display: flex; flex-wrap: wrap; gap: var(--space-xs); margin-top: var(--space-xl); }
  .filters .chip { height: 2.25rem; color: var(--color-ink-2); }
  .filters .chip b { color: var(--color-ink); }
  #f-all:checked ~ .filters label[for="f-all"], #f-auto:checked ~ .filters label[for="f-auto"], #f-draft:checked ~ .filters label[for="f-draft"], #f-human:checked ~ .filters label[for="f-human"], #f-quarantine:checked ~ .filters label[for="f-quarantine"] { background: var(--color-ink); border-color: var(--color-ink); color: var(--color-paper); }
  #f-all:checked ~ .filters label[for="f-all"] b, #f-auto:checked ~ .filters label[for="f-auto"] b, #f-draft:checked ~ .filters label[for="f-draft"] b, #f-human:checked ~ .filters label[for="f-human"] b, #f-quarantine:checked ~ .filters label[for="f-quarantine"] b { color: var(--color-paper); }
  #f-all:focus-visible ~ .filters label[for="f-all"], #f-auto:focus-visible ~ .filters label[for="f-auto"], #f-draft:focus-visible ~ .filters label[for="f-draft"], #f-human:focus-visible ~ .filters label[for="f-human"], #f-quarantine:focus-visible ~ .filters label[for="f-quarantine"] { outline: 2px solid var(--color-focus); outline-offset: 2px; }
  #f-auto:checked ~ .table-scroll tr:not(.fam-auto) { display: none; }
  #f-draft:checked ~ .table-scroll tr:not(.fam-draft) { display: none; }
  #f-human:checked ~ .table-scroll tr:not(.fam-human) { display: none; }
  #f-quarantine:checked ~ .table-scroll tr:not(.fam-quarantine) { display: none; }
  .table-scroll { margin-top: var(--space-md); overflow-x: auto; border-top: 1px solid var(--color-rule); }
  table { border-collapse: collapse; width: 100%; min-width: 56rem; }
  thead th { text-align: left; font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: 0.06em; text-transform: uppercase; font-weight: 500; color: var(--color-muted); padding: var(--space-sm) var(--space-md) var(--space-sm) 0; border-bottom: 1px solid var(--color-rule); }
  tbody td { padding: var(--space-md) var(--space-md) var(--space-md) 0; border-bottom: 1px solid var(--color-rule); vertical-align: top; font-size: var(--text-sm); }
  tbody tr:hover td { background: var(--color-paper-2); }
  .col-mail { width: 26%; } .col-cat { width: 12%; } .col-risk { width: 9%; white-space: nowrap; } .col-decision { width: 17%; }
  .subject { font-weight: 600; color: var(--color-ink); margin-top: var(--space-3xs); }
  .from { color: var(--color-muted); font-size: var(--text-sm); margin-top: var(--space-3xs); }
  .col-risk .dot { margin-right: var(--space-xs); }
  .risk-none { background: var(--color-status-good); } .risk-low { background: var(--color-status-draft); } .risk-medium { background: var(--color-status-human); } .risk-high { background: var(--color-status-quarantine); }
  .badge { display: inline-flex; align-items: center; gap: var(--space-xs); font-weight: 600; color: var(--color-ink); white-space: nowrap; }
  ul.reasons { margin: 0; padding-left: var(--space-md); color: var(--color-ink-2); }
  ul.reasons li + li { margin-top: var(--space-3xs); }
  .deadline { color: var(--color-ink); font-weight: 600; }
  details { margin-top: var(--space-xs); }
  details.more { margin-top: var(--space-2xs); } details.more summary { color: var(--color-muted); font-weight: 400; }
  summary { cursor: pointer; font-size: var(--text-sm); color: var(--color-accent); font-weight: 500; }
  pre.draft { white-space: pre-wrap; font-family: var(--font-body); font-size: var(--text-sm); line-height: 1.5; color: var(--color-ink-2); background: var(--color-paper-2); border: 1px solid var(--color-rule); border-radius: var(--radius-control); padding: var(--space-sm) var(--space-md); margin: var(--space-xs) 0 0; }

  /* F3 · spec sheet for the assumptions */
  .spec-sheet { width: 100%; border-collapse: collapse; margin-top: var(--space-xl); border-top: 1px solid var(--color-rule); }
  .spec-sheet th, .spec-sheet td { padding: var(--space-sm) var(--space-md) var(--space-sm) 0; border-bottom: 1px solid var(--color-rule); text-align: left; vertical-align: top; font-size: var(--text-sm); }
  .spec-sheet th { font-family: var(--font-display); font-weight: 600; color: var(--color-ink); white-space: nowrap; width: 6rem; font-size: var(--text-base); }
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
    .two-col { grid-template-columns: minmax(0, 1fr); gap: var(--space-xl); }
    .bar-row { grid-template-columns: minmax(0, 1fr); gap: var(--space-2xs); }
    .bar-value .sub { display: inline; margin-left: var(--space-xs); }
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


def _hero_figure(kpis: dict, level: int, total: int) -> tuple[str, str, str]:
    """Returns (figure_html, headline, label). The hero leads with the
    efficiency gain (saved_pct); only when that estimate collapses to
    nothing do we fall back to the measured auto_handled count."""
    saved_pct = kpis["saved_pct"]
    minutes_before = kpis["minutes_before"]
    if saved_pct and minutes_before:
        saved_str = _de(saved_pct, 0)
        figure_html = (
            f'<p class="figure tnum reveal" style="--i:0" aria-label="minus {saved_str} Prozent">'
            f'<span class="figure__sign">&minus;</span>'
            f'<span data-count="{saved_str}">{saved_str}</span>'
            f'<span class="figure__unit">&nbsp;%</span></p>'
        )
        headline = "weniger Bearbeitungszeit im Posteingang, bei gleicher Sicherheit."
        label = f"geschätzt &middot; Autonomiestufe {level} &middot; {total} Mails"
        return figure_html, headline, label
    n = kpis["auto_handled"]
    figure_html = (
        f'<p class="figure tnum reveal" style="--i:0" aria-label="{n}">'
        f'<span data-count="{n}">{n}</span></p>'
    )
    headline = "Mails ohne menschlichen Eingriff erledigt."
    label = "gemessen in diesem Lauf"
    return figure_html, headline, label


NAV_META_MAX_LEN = 40


def _nav_classifier_label(classifier_mode: str) -> str:
    """Short form of the classifier mode for the sticky nav bar only. The full
    label (used in step2 text and results.json/audit.jsonl) can be a long
    'openai-compatible:<host>:<model>' string, which doesn't fit the one-line
    nav meta - shorten it to 'LLM: <model>' there, best-effort-extracting the
    model name from the known label shapes."""
    if len(classifier_mode) <= NAV_META_MAX_LEN:
        return classifier_mode
    if classifier_mode.startswith("anthropic:"):
        model = classifier_mode[len("anthropic:"):]
        return f"LLM: {model}"
    if classifier_mode.startswith("openai-compatible:"):
        rest = classifier_mode[len("openai-compatible:"):]
        parts = rest.split(":")
        # host is parts[0], plus a following pure-digit port if present
        split_at = 2 if len(parts) > 1 and parts[1].isdigit() else 1
        model = ":".join(parts[split_at:]) or rest
        return f"LLM: {model}"
    return classifier_mode


def write_report_html(results: list[MailResult], kpis: dict, rampup: list[dict], level: int,
                       classifier_mode: str, out_dir: Path) -> Path:
    generated_at = datetime.now().strftime("%d.%m.%Y, %H:%M")
    m = _load_metrics_assumptions()
    tokens_css = TOKENS_PATH.read_text(encoding="utf-8")
    total = kpis["total_mails"]
    is_heuristic = classifier_mode.startswith("heuristic")
    classifier_label = "Heuristik (offline)" if is_heuristic else _nav_classifier_label(classifier_mode)
    if is_heuristic:
        step2 = ("Im Zielbild ein LLM mit strukturierter JSON-Ausgabe. <em>Dieser Lauf nutzte die "
                 "regelbasierte Offline-Heuristik.</em> Der Mail-Inhalt gilt in beiden Fällen strikt als Daten, nie als Anweisung.")
    else:
        step2 = (f"LLM-Klassifikation mit strukturierter JSON-Ausgabe (<em>{_esc(classifier_mode)}</em>). "
                 "Der Mail-Inhalt gilt strikt als Daten, nie als Anweisung.")
    threats_n = kpis["threats_blocked"]
    injections_n = kpis["injection_attempts_detected"]
    human_n = kpis["human_required"]
    figure_html, figure_headline, figure_label = _hero_figure(kpis, level, total)
    filter_inputs, filter_labels = _filter_controls(results)

    page = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mail Triage Agent &middot; Report</title>
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
      <a class="wordmark" href="#top">Mail Triage Agent</a>
      <p class="nav__meta mono">Stufe {level} &middot; {_esc(LEVEL_NAMES[level])} &middot; {_esc(classifier_label)}</p>
    </div>
    <nav aria-label="Abschnitte">
      <ul class="nav__links mono">
        <li><a href="#nutzen">Nutzen</a></li>
        <li><a href="#abwehr">Abwehr</a></li>
        <li><a href="#funktionsweise">Funktionsweise</a></li>
        <li><a href="#entscheidungen">Entscheidungen</a></li>
        <li><a href="#annahmen">Annahmen</a></li>
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
            <strong>{total} eingehende Mails</strong> für Nordlicht Solar geprüft, klassifiziert und entschieden.
            <strong>{kpis['auto_handled']} Mails</strong> ohne menschlichen Eingriff erledigt, <strong>{threats_n} Angriffe gestoppt</strong>,
            darunter {injections_n} Prompt-Injection-Versuche. Von {_de(kpis['minutes_before'])} auf {_de(kpis['minutes_after'])}&nbsp;Minuten
            Bearbeitungszeit, hochgerechnet {_de(kpis['monthly_hours_saved'], 0)}&nbsp;Stunden pro Monat.
          </p>
          <a class="chip chip--cta" href="#entscheidungen">Alle {total} Entscheidungen ansehen</a>
        </div>
      </div>
      <div class="reveal" style="--i:3">{_flow_bar(results)}</div>
    </div>
  </section>

  <div class="wrap">
    {_stat_strip(kpis)}
  </div>

  <section class="block" id="nutzen">
    <div class="wrap">
      <div class="block__head">
        <h2>Was die Automatisierung bringt</h2>
        <p>Bearbeitungszeit für genau diesen Posteingang, heute gegen morgen. Alle Zeitwerte sind Schätzungen aus den Annahmen in policy.toml.</p>
      </div>
      <div class="two-col">
        <div>
          <h3>Zeitaufwand für {total} Mails</h3>
          {_before_after(kpis)}
        </div>
        <div>
          <h3>Ersparnis je Autonomiestufe</h3>
          {_rampup_bars(rampup, level)}
          <p class="note" style="margin-top:var(--space-lg)">Gleicher Posteingang, vier Stufen. Die nächste Stufe wird erst freigegeben, wenn die Zustimmungsrate menschlicher Reviews nach 50 Prüfungen über 95&nbsp;% liegt.</p>
        </div>
      </div>
    </div>
  </section>

  <section class="block" id="abwehr">
    <div class="wrap">
      <div class="block__head">
        <h2>Abgewehrt: {threats_n} Mails in Quarantäne</h2>
        <p>Gestoppt vor jeder Antwort und unabhängig von der Autonomiestufe. Die Regel-IDs zeigen, welche Prüfung angeschlagen hat.</p>
      </div>
      {_threats(results)}
    </div>
  </section>

  <section class="band" id="funktionsweise">
    <div class="wrap">
      <div class="block__head">
        <h2>So funktioniert es</h2>
        <p>Fünf Schritte je Mail. Die Sicherheitsprüfung kommt vor jeder KI, der Leak-Guard nach jeder Antwort.</p>
      </div>
      <ol class="steps">
        <li class="step"><div><span class="stage">1.0</span><h3>Sicherheitsprüfung</h3></div><p>Regelbasiert und deterministisch: Prompt-Injection, Phishing-Links, CEO-Fraud, riskante Anhänge. Ergebnis ist ein Risikoscore mit benannten Befunden.</p></li>
        <li class="step"><div><span class="stage">2.0</span><h3>Klassifikation</h3></div><p>{step2}</p></li>
        <li class="step"><div><span class="stage">3.0</span><h3>Policy-Engine</h3></div><p>Entscheidet nach Autonomiestufe 0 bis 3. Quarantäne, Datenschutz und niedrige Konfidenz überstimmen jede Stufe.</p></li>
        <li class="step"><div><span class="stage">4.0</span><h3>Leak-Guard</h3></div><p>Jeder Antwortentwurf wird gegen interne Begriffe, IBANs und Schlüssel geprüft, bevor er vorgeschlagen wird. Ein Treffer eskaliert an den Menschen.</p></li>
        <li class="step"><div><span class="stage">5.0</span><h3>Audit und Feedback</h3></div><p>Jede Entscheidung steht mit Begründung im Audit-Log. Menschliche Reviews steuern, wann die nächste Autonomiestufe freigegeben wird.</p></li>
      </ol>
    </div>
  </section>

  <section class="block" id="entscheidungen">
    <div class="wrap">
      <div class="block__head">
        <h2>Alle {total} Entscheidungen</h2>
        <p>Kategorie, Risiko, Entscheidung und Begründung je Mail. Entwürfe wurden vom Leak-Guard freigegeben.</p>
      </div>
      {filter_inputs}
      <div class="filters" role="group" aria-label="Nach Entscheidung filtern">{filter_labels}</div>
      <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Mail</th><th>Kategorie</th><th>Risiko</th><th>Entscheidung</th><th>Begründung</th></tr>
        </thead>
        <tbody>{_decisions_table(results)}
        </tbody>
      </table>
      </div>
    </div>
  </section>

  <section class="block" id="annahmen">
    <div class="wrap">
      <div class="block__head">
        <h2>Annahmen hinter den Zeitwerten</h2>
        <p>Aus policy.toml [metrics]. Schätzungen, die eine Messung im echten Betrieb vorbereiten, nicht ersetzen.</p>
      </div>
      {_assumptions_table(m)}
      <p class="note">Kategoriespezifische Bearbeitungszeiten nach der Triage stehen in [metrics.manual_minutes]. Die Seite ist druckoptimiert.</p>
    </div>
  </section>

  <div class="wrap">
    <div class="closing">
      <p>Ergebnis dieses Laufs: {threats_n} Angriffe gestoppt, {kpis['auto_handled']} von {total} Mails automatisch erledigt,
      {human_n} bewusst beim Menschen gelassen. Kein Entwurf hat interne Informationen verlassen.</p>
      <p>Nächster Schritt: Entscheidungen mit <code>review.py</code> bestätigen oder ablehnen. Liegt die Zustimmungsrate nach 50 Reviews über 95&nbsp;%, empfiehlt das System die nächste Autonomiestufe.</p>
    </div>
  </div>
</main>

<footer class="foot-line">
  <div class="wrap">
    <span>Nordlicht Solar GmbH</span><span>info@nordlicht-solar.de</span><span>Lauf vom {_esc(generated_at)}</span><span>erzeugt von run_triage.py</span>
  </div>
</footer>
<script>{REPORT_JS}</script>
</body>
</html>
"""
    path = out_dir / "report.html"
    path.write_text(page, encoding="utf-8")
    return path


def write_all(results: list[MailResult], kpis: dict, rampup: list[dict], level: int,
              classifier_mode: str, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "results_json": write_results_json(results, kpis, rampup, level, classifier_mode, out_dir),
        "audit_jsonl": write_audit_jsonl(results, level, classifier_mode, out_dir),
        "report_html": write_report_html(results, kpis, rampup, level, classifier_mode, out_dir),
    }
