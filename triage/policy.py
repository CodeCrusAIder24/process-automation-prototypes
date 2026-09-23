"""Decision engine: turns (security screen, classification) into a Decision,
according to the configured autonomy level.

Hard rules (checked first, override every autonomy level):
  1. risk level "high" OR injection_suspected  -> QUARANTINE, no reply ever.
  2. risk level "medium"                       -> ESCALATE_HUMAN.
  3. requests_nonpublic_info                   -> never AUTO_REPLY (at most a
     DRAFT_FOR_REVIEW polite decline).
  4. category == gdpr_request                  -> never AUTO_REPLY.
  5. confidence < min_confidence                -> ESCALATE_HUMAN.

Autonomy levels (see policy.toml [autonomy]):
  0 Observe      - AI only suggests: everything -> ESCALATE_HUMAN or DRAFT_FOR_REVIEW.
  1 Assist       - newsletter/auto_reply -> AUTO_ARCHIVE; invoice/job_application/
                   meeting_request/sales_lead/gdpr_request -> AUTO_ROUTE (+ draft where
                   sensible); faq_question/order_status -> DRAFT_FOR_REVIEW;
                   complaint/unclear/internal_info_request -> ESCALATE_HUMAN.
  2 Act low-risk - as level 1, plus faq_question -> AUTO_REPLY when confidence is high
                   enough, risk is none, and the leak guard passes.
  3 Extended     - plus order_status and sales_lead acknowledgements -> AUTO_REPLY under
                   the same conditions.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from .models import Mail, SecurityScreen, Classification, Decision
from . import respond

POLICY_PATH = Path(__file__).resolve().parent.parent / "policy.toml"


def _load_policy() -> dict:
    with open(POLICY_PATH, "rb") as f:
        return tomllib.load(f)


# Categories for which level 1 draws a routing decision with an optional
# acknowledgement draft attached.
_ROUTE_WITH_DRAFT_CATEGORIES = {
    "invoice", "job_application", "meeting_request", "sales_lead", "gdpr_request",
}
_DRAFT_FOR_REVIEW_CATEGORIES = {"faq_question", "order_status"}
_ESCALATE_CATEGORIES = {"complaint", "unclear", "internal_info_request"}


def decide(mail: Mail, security: SecurityScreen, classification: Classification, level: int | None = None) -> Decision:
    policy = _load_policy()
    autonomy = policy["autonomy"]
    routing = policy["routing"]
    level = autonomy["level"] if level is None else level

    reasons: list[str] = []
    target_default = routing.get(classification.category, "Kundenservice")

    # --- Hard rule 1: risk high or injection -> QUARANTINE, always, no exceptions ---
    if security.risk_level == "high" or security.injection_suspected:
        reasons.append(f"Sicherheitsrisiko '{security.risk_level}' oder Prompt-Injection erkannt")
        for f in security.findings:
            reasons.append(f"{f.rule_id}: {f.description}")
        return Decision(
            action="QUARANTINE",
            target="Security-Quarantäne",
            reasons=reasons,
            requires_human=True,
            draft=None,
        )

    # --- Hard rule 2: risk medium -> ESCALATE_HUMAN ---
    if security.risk_level == "medium":
        reasons.append("Sicherheitsrisiko 'medium' erfordert menschliche Prüfung")
        for f in security.findings:
            reasons.append(f"{f.rule_id}: {f.description}")
        return Decision(
            action="ESCALATE_HUMAN",
            target=target_default,
            reasons=reasons,
            requires_human=True,
            draft=None,
        )

    # --- Hard rule 5: low confidence -> ESCALATE_HUMAN ---
    if classification.confidence < autonomy["min_confidence"]:
        reasons.append(
            f"Konfidenz {classification.confidence:.2f} liegt unter min_confidence "
            f"({autonomy['min_confidence']})"
        )
        return Decision(
            action="ESCALATE_HUMAN",
            target=target_default,
            reasons=reasons,
            requires_human=True,
            draft=None,
        )

    category = classification.category

    # --- Level 0: Observe - AI only ever suggests ---
    if level == 0:
        reasons.append("Autonomiestufe 0 (Observe): KI schlägt nur vor, Mensch entscheidet")
        draft = None
        action = "ESCALATE_HUMAN"
        if category in _ROUTE_WITH_DRAFT_CATEGORIES | _DRAFT_FOR_REVIEW_CATEGORIES:
            action = "DRAFT_FOR_REVIEW"
            draft, draft_ok, draft_reasons = respond.try_draft(mail, classification)
            reasons.extend(draft_reasons)
        return Decision(action=action, target=target_default, reasons=reasons, requires_human=True, draft=draft)

    # From here on we are at level >= 1.

    if category in ("newsletter", "auto_reply"):
        reasons.append(f"Kategorie '{category}' mit ausreichender Konfidenz -> automatisches Archivieren")
        return Decision(action="AUTO_ARCHIVE", target=routing.get(category, "Archiv"), reasons=reasons, requires_human=False)

    if category in _ROUTE_WITH_DRAFT_CATEGORIES:
        reasons.append(f"Kategorie '{category}' -> automatisches Routing an {target_default}")
        draft = None
        if category != "gdpr_request":  # gdpr_request must never get an auto-drafted reply
            draft, draft_ok, draft_reasons = respond.try_draft(mail, classification)
            reasons.extend(draft_reasons)
        else:
            reasons.append("GDPR-Anfrage: keine automatische Antwort, Frist beachten (1 Monat, Art. 12 DSGVO)")
        return Decision(action="AUTO_ROUTE", target=target_default, reasons=reasons, requires_human=True, draft=draft)

    if category in _DRAFT_FOR_REVIEW_CATEGORIES:
        can_auto_reply = False
        if category == "faq_question" and level >= 2:
            can_auto_reply = True
        if category == "order_status" and level >= 3:
            can_auto_reply = True

        if can_auto_reply and not classification.requests_nonpublic_info:
            if classification.confidence >= autonomy["faq_auto_reply_confidence"] and security.risk_level == "none":
                draft, draft_ok, draft_reasons = respond.try_draft(mail, classification)
                reasons.extend(draft_reasons)
                if draft_ok:
                    reasons.append(
                        f"Autonomiestufe {level}: automatische Antwort erlaubt "
                        f"(Konfidenz {classification.confidence:.2f}, Risiko none, Leak-Guard bestanden)"
                    )
                    return Decision(action="AUTO_REPLY", target=target_default, reasons=reasons, requires_human=False, draft=draft)
                reasons.append("Leak-Guard hat den Entwurf blockiert -> Eskalation an Mensch")
                return Decision(action="ESCALATE_HUMAN", target=target_default, reasons=reasons, requires_human=True, draft=None)

        reasons.append(f"Kategorie '{category}' -> Entwurf zur menschlichen Freigabe")
        draft, draft_ok, draft_reasons = respond.try_draft(mail, classification)
        reasons.extend(draft_reasons)
        return Decision(action="DRAFT_FOR_REVIEW", target=target_default, reasons=reasons, requires_human=True, draft=draft)

    if category in _ESCALATE_CATEGORIES:
        reasons.append(f"Kategorie '{category}' erfordert menschliche Entscheidung")
        draft = None
        if category == "internal_info_request":
            # Never auto-replied (requests_nonpublic_info), but it is useful to show a
            # human a ready-made, leak-guard-checked polite decline as a starting point.
            draft, draft_ok, draft_reasons = respond.try_draft(mail, classification)
            reasons.extend(draft_reasons)
        return Decision(action="ESCALATE_HUMAN", target=target_default, reasons=reasons, requires_human=True, draft=draft)

    if category == "spam_or_phishing":
        reasons.append("Als Spam/Phishing klassifiziert -> Quarantäne")
        return Decision(action="QUARANTINE", target="Security-Quarantäne", reasons=reasons, requires_human=True, draft=None)

    # Fallback for anything unforeseen.
    reasons.append("Keine passende Regel gefunden -> zur Sicherheit an Mensch eskaliert")
    return Decision(action="ESCALATE_HUMAN", target=target_default, reasons=reasons, requires_human=True, draft=None)
