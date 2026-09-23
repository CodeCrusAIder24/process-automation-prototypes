# Mail Triage Agent

A small, working prototype that reads a shared company inbox, screens every mail for
security threats (phishing, prompt injection, CEO fraud/BEC), classifies it, and decides
per mail whether to act automatically or hand it to a human - according to a configurable
**autonomy level** that can be ramped up over time as trust is earned.

Fictional company: **Nordlicht Solar GmbH** (German SME selling and installing solar
systems, shared inbox `info@nordlicht-solar.de`).

**Deutsche Kurzfassung:** Ausgangslage, Lösung und Nutzen auf Deutsch stehen in
[Kurzfassung auf Deutsch](#kurzfassung-auf-deutsch).

**Portfolio pieces:** this repo holds two prototypes. Piece 1 is this Mail Triage
Agent (below). Piece 2 is [Invoice Match](invoice_match/README.md) - automated 3-way
supplier-invoice verification (Rechnung / Bestellung / Wareneingang) for a retail
chain, with deterministic, auditable payment decisions.

## Demo in 60 seconds

```bash
pip install -r requirements.txt
python run_triage.py --no-llm
python -m pytest -q
```

Prefer reading in the browser? `python build_readme.py` renders this README to
`docs/index.html`.

You will see: a console table with all 16 sample mails, their category, risk level and
decision; two obvious attacks (phishing, CEO fraud) and two prompt-injection mails sent
straight to quarantine; KPIs for time saved; and a path to `out/report.html` - a
self-contained dashboard you can open in any browser.

---

## Starting situation (Ausgangslage)

A shared inbox such as `info@` or `support@` at a small or mid-sized company receives
roughly 50 to 150 mails a day: customer questions, quote requests, supplier invoices,
applications, meeting requests, newsletters, spam and phishing. Today one or two
back-office people read every single mail, decide what it is, who should get it and
whether it is safe, then re-type the key facts into a ticket or ERP system - a media
break (Medienbruch) - and write routine answers by hand.

The bottleneck is the triage decision itself, which touches 100 percent of the volume
although well over half of it is routine or noise. **Affected:** office staff working
interrupt-driven at two to three minutes per mail, specialists who receive their mails
hours late, customers waiting for standard answers. **Risk:** under time pressure,
phishing and social engineering slip through, and handling is inconsistent. **Worth
automating** because the decision is repetitive and well structured, volume is steady,
and the damage of a wrong decision can be capped by design with a human in the loop for
anything non-trivial.

## Solution design (Lösungsdesign)

```
                 ┌───────────────────┐
  inbox.json --> │  1. Security       │  rules-based, deterministic
 (mock mailbox)  │     screen         │  risk score 0-100, findings, sanitized_body
                 └─────────┬─────────┘
                           │
                 ┌─────────▼─────────┐
                 │  2. Classifier      │  LLM (structured JSON) with heuristic
                 │                     │  fallback; mail body = untrusted <email> data
                 └─────────┬─────────┘
                           │
                 ┌─────────▼─────────┐
                 │  3. Policy engine   │  autonomy level 0-3, hard override rules
                 │                     │  (quarantine, GDPR, low confidence, ...)
                 └─────────┬─────────┘
                           │
                 ┌─────────▼─────────┐
                 │  4. Reply drafting  │  templates from public_faq.md only
                 │     + leak guard    │  blocks internal terms/IBAN/API keys/URLs
                 └─────────┬─────────┘
                           │
                 ┌─────────▼─────────┐
                 │  5. Report / audit  │  console, results.json, audit.jsonl,
                 │                     │  report.html, feedback loop (review.py)
                 └────────────────────┘
```

**Why this approach.** A rules-first security screen catches structural attack patterns
(instruction-override phrases, link/domain mismatches, display-name spoofing) whether or
not any LLM is even involved, and its verdict is treated as authoritative. The LLM
classifier only runs on sanitized text, is told explicitly that the mail content is
untrusted data it must never obey, and is asked to self-report suspected injection as a
second, independent signal. The policy engine keeps the actual business rules
data-driven and inspectable, with a small set of hard overrides that no autonomy level
can bypass. A leak guard checks every piece of outbound text against a denylist built
from internal-only information before anything is proposed as a reply. Every decision is
logged to an audit trail, and a human-feedback loop tracks whether the agent's decisions
are actually agreed with before autonomy is increased.

**Rejected alternatives:**
- *Pure rules, no classifier* - cannot read intent or handle the long tail of phrasing;
  either over-blocks legitimate mail or misses novel wording.
- *Fully autonomous LLM agent with send-mail tools* - unbounded blast radius on day one;
  a single successful prompt injection could leak data or send money. Not acceptable for
  a first deployment.
- *n8n / Power Automate alone* - fine as a trigger/transport layer later, but the
  classification and security logic must be inspectable, testable, and versionable in
  code, not buried in a low-code canvas.

The LLM is a replaceable component behind an OpenAI-compatible interface, so the same
pipeline runs against a hosted API, a local model or an internal company server.

### Security by design

- **Prompt injection:** deterministic rules (`INJ-01/02/03`) detect instruction-override
  phrases, role/system markers and exfiltration asks in both German and English; the
  mail body is sanitized before it reaches the LLM; the LLM is separately instructed to
  treat all mail content as untrusted data and to self-report injection attempts. Either
  signal being true sets `injection_suspected = true`.
- **Phishing / BEC indicators:** link-text vs. href mismatches and lookalike domains
  (`PHI-02`), credential/urgency lures (`PHI-01`), display-name spoofing of an executive
  from an external domain (`BEC-01`), and payment/bank-detail-change requests combined
  with urgency (`BEC-02`).
- **Leak guard:** every drafted reply is checked against a denylist derived from
  `knowledge/internal_confidential.md` (codenames, prices, customer names, IBAN, API
  keys, internal URLs) plus generic patterns (IBAN regex, `sk-...` keys, `intern.*`
  URLs). A failing check downgrades the decision to `ESCALATE_HUMAN` - the draft is
  never sent.
- **Quarantine overrides everything.** Risk level `high` or `injection_suspected` always
  wins over the configured autonomy level, at every level from 0 to 3.
- **No send capability.** This prototype never sends a single mail. Every "reply" is a
  draft written to `results.json` for a human (or a future, separately-approved outbox)
  to send.

### Autonomy levels / ramp-up

| Level | Name          | Behaviour                                                                                                   |
|-------|---------------|---------------------------------------------------------------------------------------------------------------|
| 0     | Observe       | AI only suggests; everything -> `ESCALATE_HUMAN` or `DRAFT_FOR_REVIEW`. Security findings still shown.       |
| 1     | Assist        | Newsletter/auto-reply -> auto-archive; invoice/job application/meeting/sales lead/GDPR -> auto-route (+ draft); FAQ/order status -> draft for review; complaint/unclear/internal-info-request -> escalate. |
| 2     | Act low-risk  | As level 1, plus FAQ questions -> auto-reply when confidence ≥ 0.90, risk `none`, and the leak guard passes.  |
| 3     | Extended      | Plus order-status acknowledgements -> auto-reply under the same conditions.                                   |

Hard rules override every level (see `triage/policy.py`): risk `high` or injection ->
quarantine; risk `medium` -> escalate; requests for non-public info never get an
auto-reply; GDPR requests never get an auto-reply; confidence below `min_confidence`
always escalates.

**Trust rule (`review.py`):** ramp-up to the next level is only recommended once at
least `min_reviewed` (default 50) human reviews have accumulated and the agreement rate
is at least `min_agreement` (default 95%) - see `policy.toml [trust]`.

**Reply wording as data, not code.** The actual German sentences sent in a draft live in
plain text files under `templates/*.txt` (one per category, plus shared `_greeting.txt` /
`_signoff.txt`), not hardcoded in `triage/respond.py`. Each file starts with a `#`-comment
header documenting which category it answers and which `{placeholder}` fields it can use;
facts are filled in from `knowledge/public_faq.md` only. A non-developer can reword a
reply by editing a `.txt` file - no Python change needed. Templates never see the mail
body or sender address (only the subject, mail id, and public FAQ facts), and every
rendered draft still passes through the leak guard (`check_outbound`) before it is
proposed - an unresolved placeholder is reported as a reason rather than silently sent or
raising an exception.

## Value delivered (Nutzen)

Numbers below are from an actual run of `python run_triage.py --no-llm` over the 16
sample mails at autonomy level 1 (heuristic classifier, fully offline). **These are
estimates**, based on the assumptions in `policy.toml [metrics]`, not measured production
data:

- `triage_minutes` (1.5) - manual read + decide + forward, spent on **every** mail in the
  baseline (before automation), regardless of category.
- category-specific `manual_minutes` - the handling time *after* triage (writing a
  reply, booking a meeting, filing an invoice, ...).
- `assisted_triage_minutes` (0.5) - even when a human still makes the final call, they
  only have to glance at the AI's category + summary instead of triaging from scratch.
  **Exception at level 0 (Observe):** the human is checking the AI, not relying on it, so
  every mail is still read in full (`triage_minutes`) and quarantines are confirmed at
  full reading cost too. Only prepared drafts earn a time credit at level 0, which is why
  the saving there comes almost entirely from drafting.
- `draft_review_minutes` (1.0), `quarantine_confirm_minutes` (0.5) and
  `auto_spot_check_minutes` (0.2) - the residual human effort for a reviewed draft, a
  confirmed quarantine, and a spot-checked auto-routed mail respectively.

Every mail is priced baseline = `triage_minutes + manual_minutes[category]`, and priced
after-automation according to the decision it actually received (see
`triage/metrics.py` for the exact per-decision formula). This is why even autonomy level
0 ("Observe", nothing is sent automatically) still shows a real but *smaller* saving than
level 1-3: the human is spared the from-scratch triage step and, where a draft was
generated, most of the drafting time - but still fully owns every decision and every
send.

| KPI (level 1, this run) | Value |
|---|---|
| Mails total | 16 |
| Auto-handled | 7 (43.8%) |
| Human required | 9 (56.2%) |
| Threats blocked (quarantined) | 4 |
| Injection attempts detected | 2 |
| Minutes before (baseline) | 63.5 |
| Minutes after | 24.5 |
| Minutes saved | 39.0 (61.4%) |
| Extrapolated at 80 mails/day, 21 working days/month | **68.2 hours saved/month** |

### Ramp-up curve (what-if, same mail set, levels 0-3)

| Level | Auto-handled | Time saved | Hours saved / month |
|---|---|---|---|
| 0 Observe | 0.0% | 30.7% | 34.1 h |
| 1 Assist | 43.8% | 61.4% | 68.2 h |
| 2 Act low-risk | 50.0% | 63.6% | 70.7 h |
| 3 Extended | 56.2% | 65.8% | 73.2 h |

Qualitative value beyond the raw minutes: **consistent handling** of every mail
regardless of who is on shift or how busy they are; a **full audit trail**
(`out/audit.jsonl`) of every decision and why it was made; **threats caught** that a
tired human might click through under time pressure; and **faster routing** to the right
department (`Vertrieb`, `Buchhaltung`, `Personal (HR)`, `Datenschutz`, ...) without
manual triage.

## Next steps to production

Connect a real mailbox via Microsoft Graph or IMAP in **read-only** mode first (see the
adapter-interface comment in `triage/ingest.py`), and only ever send mail through a
separate, human-approved outbox - never automatically from the classifier/policy path;
keep secrets (API keys, mailbox credentials) in a proper vault, not environment
variables; build a labelled evaluation set of real mails and run it as a regression test
before every policy change; monitor the human-agreement rate and watch for drift over
time; maintain a red-team set of injection/phishing/BEC samples that must stay at 100%
detection as a release gate; do a GDPR (DSGVO) review covering data minimization and
EU-hosted or zero-retention API terms for any LLM calls; integrate with the real
ticket/ERP system to close the media break instead of just labelling category and
department; and keep a one-command rollback switch back to autonomy level 0 at all
times.

## Kurzfassung auf Deutsch

**Ausgangslage:** Ein gemeinsames Postfach wie `info@` oder `support@` erhält in einem
kleinen oder mittleren Unternehmen täglich 50 bis 150 E-Mails: Kundenanfragen,
Angebotswünsche, Lieferantenrechnungen, Bewerbungen, Terminanfragen, Newsletter, Spam und
Phishing. Heute lesen ein bis zwei Mitarbeitende jede einzelne Mail, entscheiden, worum
es geht, wer zuständig ist und ob die Mail sicher ist, übertragen die wichtigsten
Informationen manuell in ein Ticket- oder ERP-System und beantworten Routinefragen von
Hand. Der Engpass ist die Triage-Entscheidung selbst, die zwar auf 100 Prozent des
Volumens angewendet wird, obwohl mehr als die Hälfte davon Routine oder Rauschen ist.
Unter Zeitdruck können Phishing- und Social-Engineering-Versuche unbemerkt durchrutschen.

**Lösung:** Ein mehrstufiger Ansatz statt einer einzigen "smarten" KI: eine
regelbasierte Sicherheitsprüfung erkennt Prompt-Injection-, Phishing- und
CEO-Fraud-Muster unabhängig von der KI; ein LLM-Klassifikator kategorisiert jede Mail
anhand strukturierter JSON-Ausgabe und behandelt den Mail-Inhalt dabei ausdrücklich als
nicht vertrauenswürdige Daten; eine Policy-Engine trifft die eigentliche
Automatisierungsentscheidung anhand einer konfigurierbaren Autonomiestufe, wobei feste
Regeln (z.B. Quarantäne bei Risiko) jede Stufe überstimmen; ein Leak-Guard verhindert,
dass jemals interne Informationen in einer automatisch erstellten Antwort landen; und
ein Audit-Log sowie eine Feedback-Schleife machen jede Entscheidung nachvollziehbar und
das System schrittweise vertrauenswürdiger.

**Nutzen:** Im Testlauf mit 16 Beispiel-Mails auf Autonomiestufe 1 wurden 43,8 Prozent
der Mails automatisch bearbeitet, vier Bedrohungen (darunter zwei Prompt-Injection- und
ein CEO-Fraud-Versuch) automatisch in Quarantäne verschoben, und geschätzt 61,4 Prozent
der Bearbeitungszeit eingespart - hochgerechnet rund 68,2 Stunden pro Monat bei
80 Mails/Tag. Darüber hinaus sorgt das System für einheitliche Bearbeitung, einen
vollständigen Audit-Trail und schnellere Weiterleitung an die richtige Abteilung.

## Project structure

```
README.md
requirements.txt
policy.toml                    # all tunables: weights, thresholds, routing, metrics, trust
tokens.css                     # design tokens for the HTML report (inlined by triage/report.py)
build_readme.py                # renders README.md -> docs/index.html (same design tokens)
run_triage.py                  # CLI entry point
review.py                      # human feedback loop CLI
triage/
  __init__.py
  models.py                    # Mail, SecurityScreen, Classification, Decision dataclasses
  ingest.py                    # JSON inbox loader (+ adapter-interface comment)
  security.py                  # rules-based screen -> risk score, level, findings
  classify.py                  # provider-agnostic LLM classifier (Anthropic + OpenAI-compatible) with heuristic fallback
  policy.py                    # decision engine by autonomy level
  respond.py                   # reply drafting from public knowledge + leak guard
  metrics.py                   # before/after time-savings estimates
  report.py                    # console table, results.json, audit.jsonl, report.html
knowledge/
  public_faq.md                 # safe to use in replies / LLM prompts
  internal_confidential.md      # NEVER sent to any LLM; leak-guard denylist source only
templates/                     # reply wording, editable without code; placeholders documented in each file
samples/inbox.json              # 16 example mails
tests/                           # pytest suite
  test_llm_backend.py            # OpenAI-compatible backend + resolve_provider, offline (stdlib http.server)
out/                             # generated: results.json, audit.jsonl, report.html
docs/index.html                  # generated: README rendered to HTML by build_readme.py
.env.example                     # copy to .env: pick one of four ready-made LLM configs
```

### Running with a real LLM

The classifier is provider-agnostic: one backend (`classify_with_openai_compatible` in
`triage/classify.py`) speaks the OpenAI chat-completions shape - a base URL plus a model
name, stdlib `urllib` only, no vendor SDK - so the exact same pipeline runs unmodified
against a hosted API, a local model, or an internal company server. A separate Anthropic
backend is kept for calling Claude directly. Which one runs is picked by
`--provider {auto,anthropic,openai,heuristic}` (default `auto`) or the
`TRIAGE_LLM_PROVIDER` env var; `auto` picks `openai` if `TRIAGE_LLM_BASE_URL` is set, else
`anthropic` if `ANTHROPIC_API_KEY` is set, else the offline heuristic - so the demo keeps
running with no configuration at all.

Transient errors and rate limits (HTTP 429/5xx, or a network-level timeout) are retried
using the provider's own `Retry-After`/`x-ratelimit-reset-*` headers to size the wait, up
to 3 attempts with a 60 s cap per wait (both configurable, see `LLM_MAX_ATTEMPTS`/
`TRIAGE_LLM_MAX_ATTEMPTS` and `LLM_MAX_WAIT_SECONDS` in `triage/classify.py`). JSON-mode
validation failures (a reasoning model spends its token budget on reasoning instead of
JSON) are recovered from the provider's `failed_generation` field when it already contains
a usable classification, or by retrying once without `response_format`. Either way, the
provider's own error text ends up in the per-mail reasons/labels, so misconfiguration -
wrong model name, a blocked `User-Agent`, an expired key - is visible instead of a bare
"HTTP 400: Bad Request". Note that a free tier such as Groq's only exposes a subset of
models (e.g. `openai/gpt-oss-120b`, `qwen/qwen3.8-27b` as of writing) - check your
console's Limits page and pick one of those rather than an arbitrary model id.

```bash
copy .env.example .env
# edit .env: uncomment exactly one option (Ollama is active by default)
python run_triage.py
```

`.env.example` ships four ready-made configs, uncomment one:

| Option | Trade-off |
|---|---|
| A - Ollama (local) | Free, private, DSGVO-friendly - keeps mail content on your machine. Needs an install and a model pull; slower than a hosted API on modest hardware. |
| B - OpenRouter `:free` models | Zero install, no card. Rate-limited (~50 requests/day), the free lineup rotates, and prompts may be used for training - fine for these fictional demo mails, not for real customer mail. |
| C - Groq | Fast free tier, OpenAI-compatible, no local install. |
| D - Anthropic API | Best quality of the four, paid, calls Claude directly (not through the OpenAI-compatible path). |

`.env` is loaded by a minimal stdlib loader in `run_triage.py` (no dependency) and never
overrides a variable already set in your real environment; it is git-ignored so a key
never ends up in a commit. Whichever backend ran - or that none did and the heuristic
fallback kept the demo running fully offline - is shown in the console header and in the
report (`out/report.html`), e.g. `openai-compatible:localhost:11434:qwen2.5:7b`; any LLM
error (unreachable server, bad response) falls back to the heuristic automatically, with
a one-time console notice rather than one per mail.

You can still call the Anthropic API directly (Option D above, or without `.env` at all):

```bash
# Windows (persists across sessions):
setx ANTHROPIC_API_KEY "sk-ant-..."
setx TRIAGE_MODEL "claude-haiku-4-5-20251001"   # optional, this is already the default

# or just for the current PowerShell session:
$env:ANTHROPIC_API_KEY = "sk-ant-..."

python run_triage.py --provider anthropic
```
