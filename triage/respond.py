"""Reply drafting from PUBLIC knowledge only, plus an outbound leak guard.

Drafts are built from small string templates in German, filled only with
facts parsed out of knowledge/public_faq.md. No LLM call is required for
drafting (templates are good enough for this prototype and are trivially
auditable).

The leak guard loads knowledge/internal_confidential.md - but ONLY to build
a denylist of terms that must never appear in outbound text. That file's
content itself is never placed into a template or sent anywhere.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import Mail, Classification

BASE_DIR = Path(__file__).resolve().parent.parent
PUBLIC_FAQ_PATH = BASE_DIR / "knowledge" / "public_faq.md"
INTERNAL_DENYLIST_PATH = BASE_DIR / "knowledge" / "internal_confidential.md"
TEMPLATES_DIR = BASE_DIR / "templates"

_KEY_VALUE_RE = re.compile(r"^([a-z_]+):\s*(.+)$", re.IGNORECASE)


def load_public_facts() -> dict[str, str]:
    """Parse the simple `key: value` lines at the top of public_faq.md."""
    facts: dict[str, str] = {}
    if not PUBLIC_FAQ_PATH.exists():
        return facts
    for line in PUBLIC_FAQ_PATH.read_text(encoding="utf-8").splitlines():
        m = _KEY_VALUE_RE.match(line.strip())
        if m:
            facts[m.group(1).strip().lower()] = m.group(2).strip()
    return facts


# --- leak guard -----------------------------------------------------------

IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[ ]?(?:\d{4}[ ]?){3,5}\d{0,4}\b")
API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9\-_]{6,}\b|api[_-]?key\s*[:=]\s*\S+", re.IGNORECASE)
INTERNAL_URL_RE = re.compile(r"https?://(?:intern\.|admin\.)[\w.\-]+", re.IGNORECASE)


def _denylist_terms() -> list[str]:
    """Build a list of literal terms/phrases from internal_confidential.md that
    must never appear in outbound text. We only ever read this file to build
    the denylist - its content is not otherwise used anywhere in the pipeline.
    """
    terms: list[str] = []
    if not INTERNAL_DENYLIST_PATH.exists():
        return terms
    for raw_line in INTERNAL_DENYLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("##"):
            continue
        # Strip simple "Label: value" prefixes so both the label-free value and
        # meaningful phrases become denylist entries.
        if ":" in line:
            _, _, value = line.partition(":")
            value = value.strip()
            if value:
                terms.append(value)
        else:
            terms.append(line)
    return [t for t in terms if len(t) >= 4]


_DENYLIST_CACHE: list[str] | None = None


def _get_denylist() -> list[str]:
    global _DENYLIST_CACHE
    if _DENYLIST_CACHE is None:
        _DENYLIST_CACHE = _denylist_terms()
    return _DENYLIST_CACHE


def check_outbound(text: str) -> tuple[bool, list[str]]:
    """Check a candidate outbound text against the leak guard.

    Returns (ok, hits) where ok is False if anything sensitive was found;
    hits is a list of human-readable reasons.
    """
    hits: list[str] = []
    low = text.lower()

    for term in _get_denylist():
        if term.lower() in low:
            hits.append(f"enthält vertraulichen Begriff aus internal_confidential.md: '{term}'")

    if IBAN_RE.search(text):
        hits.append("enthält eine IBAN-ähnliche Zeichenfolge")

    if API_KEY_RE.search(text):
        hits.append("enthält einen API-Key-ähnlichen String")

    if INTERNAL_URL_RE.search(text):
        hits.append("enthält eine interne Admin-URL")

    return (len(hits) == 0, hits)


# --- templates --------------------------------------------------------------
#
# Reply wording lives in plain text files under templates/ so non-developers
# can edit it without touching code. Each file uses str.format placeholders;
# see load_template()/render_template() below for what is filled in. The mail
# body and sender address are intentionally never exposed to templates -
# only a small set of safe mail fields (subject, id) plus public FAQ facts.

_CATEGORY_TEMPLATE_FILES = {
    "faq_question": "faq_question.txt",
    "sales_lead": "sales_lead.txt",
    "order_status": "order_status.txt",
    "invoice": "invoice.txt",
    "meeting_request": "meeting_request.txt",
    "job_application": "job_application.txt",
    "gdpr_request": "gdpr_request.txt",
    "internal_info_request": "internal_info_decline.txt",
}

# Defaults mirror knowledge/public_faq.md so a missing fact still renders a
# sensible reply instead of a visibly broken one.
_FACT_DEFAULTS = {
    "opening_hours": "Mo-Fr 08:00-17:00 Uhr",
    "service_area": "Wir sind in ganz Norddeutschland tätig",
    "quote_request": "Bitte senden Sie uns Dachfläche, Ausrichtung und gewünschte Leistung",
    "response_time": "Wir antworten in der Regel innerhalb von 2 Werktagen",
    "gdpr_contact": "datenschutz@nordlicht-solar.de",
}

_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class _SafeDict(dict):
    """A format_map dict that leaves unknown placeholders visible instead of
    raising, so a typo in a template renders as `{typo}` rather than crashing.
    """

    def __missing__(self, key):
        return "{" + key + "}"


def _as_sentence(text: str, suffix: str = "") -> str:
    """Strip a trailing '.', optionally append suffix, then add exactly one '.'.

    Used to safely compose FAQ facts (which are stored without a trailing
    full stop) into a single clean sentence, whatever suffix is added.
    """
    text = text.strip().rstrip(".")
    return f"{text}{suffix}."


def load_template(name: str) -> str:
    """Load a template file from templates/, stripping its leading '#' header.

    Comment lines at the top of the file (starting with '#') are documentation
    for editors and are not part of the rendered text.
    """
    text = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith("#"):
        i += 1
    return "\n".join(lines[i:]).strip()


def _template_context(mail: Mail, facts: dict[str, str]) -> dict[str, str]:
    """Build the safe substitution context available to every template.

    Only the mail subject and id are exposed from the mail itself - never the
    body or sender address, since those are untrusted input. Facts come from
    knowledge/public_faq.md (public information only), both raw and as a
    normalised, period-terminated sentence.
    """
    ctx: dict[str, str] = {
        "greeting": load_template("_greeting.txt"),
        "signoff": load_template("_signoff.txt"),
        "mail_subject": mail.subject,
        "mail_id": mail.id,
    }
    for key, default in _FACT_DEFAULTS.items():
        ctx[key] = facts.get(key, default)
    for key, value in facts.items():
        ctx.setdefault(key, value)

    ctx["opening_hours_sentence"] = _as_sentence(ctx["opening_hours"])
    ctx["service_area_sentence"] = _as_sentence(ctx["service_area"])
    ctx["quote_request_sentence"] = _as_sentence(ctx["quote_request"])
    ctx["response_time_sentence"] = _as_sentence(
        ctx["response_time"], " mit einem unverbindlichen Angebot"
    )
    return ctx


def render_template(name: str, mail: Mail, facts: dict[str, str]) -> str:
    """Load templates/<name> and fill it in with the safe template context."""
    template_str = load_template(name)
    ctx = _template_context(mail, facts)
    return template_str.format_map(_SafeDict(ctx))


def draft_reply(mail: Mail, classification: Classification) -> str | None:
    """Build a draft reply for categories that have a template, else None."""
    template_name = _CATEGORY_TEMPLATE_FILES.get(classification.category)
    if template_name is None:
        return None
    facts = load_public_facts()
    return render_template(template_name, mail, facts)


def try_draft(mail: Mail, classification: Classification) -> tuple[str | None, bool, list[str]]:
    """Draft a reply and immediately run it through the leak guard.

    Returns (draft_text_or_None, ok, reasons). If a draft was built but the
    leak guard blocked it, draft_text_or_None is None and ok is False, with
    reasons explaining why.
    """
    draft = draft_reply(mail, classification)
    if draft is None:
        return None, True, []

    ok, hits = check_outbound(draft)
    if not ok:
        reasons = ["Leak-Guard hat Entwurf blockiert: " + "; ".join(hits)]
        return None, False, reasons

    reasons = [f"Entwurf erstellt (Kategorie: {classification.category})"]
    if classification.category == "order_status":
        reasons.append(
            "ERP-Abgleich für den Bestellstatus ist im Prototyp ein Stub; "
            "Status vor dem Versand manuell prüfen"
        )

    unresolved = sorted(set(_UNRESOLVED_PLACEHOLDER_RE.findall(draft)))
    for key in unresolved:
        reasons.append(f"Template-Platzhalter unbekannt: {{{key}}}")

    return draft, True, reasons
