import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.models import Mail, SecurityScreen, Classification
from triage import policy
from triage import security as security_mod
from triage import classify as classify_mod
from triage import metrics as metrics_mod
from triage.ingest import load_inbox

BASE_DIR = Path(__file__).resolve().parent.parent
INBOX_PATH = BASE_DIR / "samples" / "inbox.json"


def make_mail(**kwargs) -> Mail:
    defaults = dict(
        id="TEST",
        from_addr="someone@example.com",
        from_name="Someone",
        to="info@nordlicht-solar.de",
        subject="Test",
        body="Test body",
        received_at="2026-09-10T10:00:00+02:00",
        attachments=[],
        links=[],
    )
    defaults.update(kwargs)
    return Mail(**defaults)


def make_security(**kwargs) -> SecurityScreen:
    defaults = dict(risk_score=0, risk_level="none", findings=[], injection_suspected=False, sanitized_body="body")
    defaults.update(kwargs)
    return SecurityScreen(**defaults)


def make_classification(**kwargs) -> Classification:
    defaults = dict(
        category="faq_question",
        confidence=0.95,
        summary="test",
        language="de",
        sentiment="neutral",
        injection_suspected=False,
        requests_nonpublic_info=False,
        suggested_department="Kundenservice",
        classifier="heuristic",
    )
    defaults.update(kwargs)
    return Classification(**defaults)


def test_quarantine_overrides_level_3():
    mail = make_mail()
    sec = make_security(risk_score=90, risk_level="high", injection_suspected=True)
    cls = make_classification(category="spam_or_phishing", confidence=0.95)
    decision = policy.decide(mail, sec, cls, level=3)
    assert decision.action == "QUARANTINE"
    assert decision.draft is None


def test_level_0_never_auto_acts():
    mail = make_mail()
    sec = make_security()
    for category in ["faq_question", "newsletter", "sales_lead", "invoice", "auto_reply"]:
        cls = make_classification(category=category, confidence=0.95)
        decision = policy.decide(mail, sec, cls, level=0)
        assert decision.action in ("ESCALATE_HUMAN", "DRAFT_FOR_REVIEW"), (
            f"level 0 auto-acted for category {category}: {decision.action}"
        )
        assert decision.requires_human is True


def test_level_1_newsletter_auto_archives():
    mail = make_mail()
    sec = make_security()
    cls = make_classification(category="newsletter", confidence=0.9)
    decision = policy.decide(mail, sec, cls, level=1)
    assert decision.action == "AUTO_ARCHIVE"


def test_level_1_faq_is_draft_not_auto_reply():
    mail = make_mail()
    sec = make_security()
    cls = make_classification(category="faq_question", confidence=0.95)
    decision = policy.decide(mail, sec, cls, level=1)
    assert decision.action == "DRAFT_FOR_REVIEW"


def test_level_2_high_confidence_faq_auto_replies():
    mail = make_mail(
        subject="Öffnungszeiten",
        body="Guten Tag, wie sind Ihre Öffnungszeiten?",
    )
    sec = make_security()
    cls = make_classification(category="faq_question", confidence=0.95)
    decision = policy.decide(mail, sec, cls, level=2)
    assert decision.action == "AUTO_REPLY"


def test_requests_nonpublic_info_never_auto_replies():
    mail = make_mail(
        subject="Frage zur Marge",
        body="Was ist Ihre Einkaufsmarge?",
    )
    sec = make_security()
    cls = make_classification(
        category="internal_info_request", confidence=0.9, requests_nonpublic_info=True
    )
    for level in (0, 1, 2, 3):
        decision = policy.decide(mail, sec, cls, level=level)
        assert decision.action != "AUTO_REPLY"


def test_gdpr_never_auto_replies():
    mail = make_mail(
        subject="Auskunftsersuchen Art. 15 DSGVO",
        body="Bitte Auskunft nach Art. 15 DSGVO.",
    )
    sec = make_security()
    cls = make_classification(category="gdpr_request", confidence=0.95)
    for level in (0, 1, 2, 3):
        decision = policy.decide(mail, sec, cls, level=level)
        assert decision.action != "AUTO_REPLY"


def test_low_confidence_escalates():
    mail = make_mail()
    sec = make_security()
    cls = make_classification(category="faq_question", confidence=0.3)
    decision = policy.decide(mail, sec, cls, level=2)
    assert decision.action == "ESCALATE_HUMAN"


def test_medium_risk_escalates():
    mail = make_mail()
    sec = make_security(risk_score=50, risk_level="medium")
    cls = make_classification(category="faq_question", confidence=0.95)
    decision = policy.decide(mail, sec, cls, level=2)
    assert decision.action == "ESCALATE_HUMAN"


def test_rampup_saved_pct_strictly_increases_on_sample_inbox():
    """Ramp-up should be a real curve, not flat: each autonomy level 0-3 must save
    strictly more time than the one before it, on the actual sample inbox."""
    mails = load_inbox(INBOX_PATH)
    security_results = [security_mod.screen(m) for m in mails]
    classification_results = [
        classify_mod.classify(m, sec, use_llm=False) for m, sec in zip(mails, security_results)
    ]
    rampup = metrics_mod.compute_rampup(mails, security_results, classification_results)
    saved_pcts = [r["saved_pct"] for r in rampup]
    assert saved_pcts == sorted(saved_pcts), f"ramp-up saved_pct not increasing: {saved_pcts}"
    assert len(set(saved_pcts)) == len(saved_pcts), f"ramp-up saved_pct has ties: {saved_pcts}"
