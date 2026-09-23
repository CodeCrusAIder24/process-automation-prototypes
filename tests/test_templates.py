import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage import respond
from triage.models import Mail, Classification

# Exact drafts produced by the pre-refactor, hardcoded f-string templates for
# mails M01 (faq_question) and M02 (sales_lead) from samples/inbox.json,
# captured by running `python run_triage.py --no-llm` before the templates
# were moved to templates/*.txt. The refactor must not change this output.
M01_DRAFT = (
    "Sehr geehrte Damen und Herren,\n\n"
    "vielen Dank für Ihre Anfrage. Unsere Öffnungszeiten: Montag bis Freitag, "
    "08:00 - 17:00 Uhr. Wir beliefern und installieren in ganz Norddeutschland, "
    "unter anderem Hamburg, Bremen, Kiel, Lübeck und das Umland.\n\n"
    "Bei weiteren Fragen sind wir gerne für Sie da.\n\n"
    "Mit freundlichen Grüßen\nIhr Nordlicht Solar Team"
)
M02_DRAFT = (
    "Sehr geehrte Damen und Herren,\n\n"
    "vielen Dank für Ihr Interesse an einer Photovoltaikanlage. Angebote können "
    "über das Kontaktformular auf www.nordlicht-solar.de/angebot oder per E-Mail "
    "mit Angabe von Dachfläche, Ausrichtung und gewünschter Leistung angefragt "
    "werden. Wir antworten in der Regel innerhalb von 2 Werktagen mit einem "
    "unverbindlichen Angebot.\n\n"
    "Mit freundlichen Grüßen\nIhr Nordlicht Solar Team"
)


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


def test_every_template_renders_without_unresolved_placeholders():
    mail = make_mail(subject="Öffnungszeiten und Servicegebiet", id="M01")
    facts = respond.load_public_facts()
    for category, filename in respond._CATEGORY_TEMPLATE_FILES.items():
        rendered = respond.render_template(filename, mail, facts)
        assert rendered, f"{filename} rendered empty text"
        unresolved = respond._UNRESOLVED_PLACEHOLDER_RE.findall(rendered)
        assert unresolved == [], (
            f"{filename} left unresolved placeholders: {unresolved}"
        )


def test_faq_question_draft_matches_pre_refactor_output():
    mail = make_mail(id="M01", subject="Öffnungszeiten und Servicegebiet")
    cls = make_classification(category="faq_question")
    draft = respond.draft_reply(mail, cls)
    assert draft == M01_DRAFT


def test_sales_lead_draft_matches_pre_refactor_output():
    mail = make_mail(id="M02", subject="Anfrage Angebot 8 kWp Anlage mit Speicher")
    cls = make_classification(category="sales_lead")
    draft = respond.draft_reply(mail, cls)
    assert draft == M02_DRAFT


def test_all_rendered_templates_pass_leak_guard():
    mail = make_mail(id="M99", subject="Beliebiger Betreff")
    facts = respond.load_public_facts()
    for filename in respond._CATEGORY_TEMPLATE_FILES.values():
        rendered = respond.render_template(filename, mail, facts)
        ok, hits = respond.check_outbound(rendered)
        assert ok is True, f"{filename} failed leak guard: {hits}"
        assert hits == []


def test_unknown_placeholder_reports_reason_and_does_not_raise(monkeypatch, tmp_path):
    # The shared greeting/signoff files must also exist under the (monkeypatched)
    # templates dir, since render_template loads them for every template.
    (tmp_path / "_greeting.txt").write_text(
        (respond.TEMPLATES_DIR / "_greeting.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "_signoff.txt").write_text(
        (respond.TEMPLATES_DIR / "_signoff.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    broken_template = tmp_path / "broken.txt"
    broken_template.write_text(
        "# Kategorie: test\n"
        "# Platzhalter: {greeting}, {signoff}\n"
        "# Nur öffentliche Informationen aus knowledge/public_faq.md verwenden.\n\n"
        "{greeting}\n\nDieser Text hat einen {nicht_existierender_platzhalter}.\n\n{signoff}",
        encoding="utf-8",
    )
    monkeypatch.setattr(respond, "TEMPLATES_DIR", tmp_path)
    monkeypatch.setitem(respond._CATEGORY_TEMPLATE_FILES, "faq_question", "broken.txt")

    mail = make_mail(id="M99", subject="Test")
    cls = make_classification(category="faq_question")

    draft, ok, reasons = respond.try_draft(mail, cls)

    assert draft is not None
    assert ok is True
    assert "{nicht_existierender_platzhalter}" in draft
    assert any(
        "Template-Platzhalter unbekannt" in r and "nicht_existierender_platzhalter" in r
        for r in reasons
    )
