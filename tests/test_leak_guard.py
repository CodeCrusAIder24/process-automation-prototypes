import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage import respond
from triage.models import Mail, Classification


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


def test_leak_guard_blocks_internal_codename():
    text = "Hier ein Update: Projekt Polarstern liegt im Zeitplan."
    ok, hits = respond.check_outbound(text)
    assert ok is False
    assert any("Polarstern" in h for h in hits)


def test_leak_guard_blocks_iban():
    text = "Bitte ueberweisen Sie an IBAN DE89 3704 0044 0532 0130 00."
    ok, hits = respond.check_outbound(text)
    assert ok is False
    assert any("IBAN" in h for h in hits)


def test_leak_guard_blocks_api_key():
    text = "Der Zugang laeuft ueber sk-nls-internal-8f27ab61c9e4."
    ok, hits = respond.check_outbound(text)
    assert ok is False


def test_leak_guard_passes_clean_faq_reply():
    mail = make_mail(subject="Öffnungszeiten", body="Wie sind Ihre Öffnungszeiten?")
    cls = make_classification(category="faq_question")
    draft = respond.draft_reply(mail, cls)
    assert draft is not None
    ok, hits = respond.check_outbound(draft)
    assert ok is True
    assert hits == []


def test_try_draft_blocks_and_downgrades_on_internal_terms(monkeypatch):
    mail = make_mail(subject="Frage", body="Was ist Ihre Marge?")
    cls = make_classification(category="internal_info_request", requests_nonpublic_info=True)

    def leaky_draft(mail, classification):
        return "Sehr geehrte Damen und Herren, Details zu Projekt Polarstern anbei."

    monkeypatch.setattr(respond, "draft_reply", leaky_draft)
    draft, ok, reasons = respond.try_draft(mail, cls)
    assert draft is None
    assert ok is False
    assert any("Leak-Guard" in r for r in reasons)
