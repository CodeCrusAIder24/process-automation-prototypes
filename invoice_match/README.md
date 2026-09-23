# Invoice Match - automated supplier invoice verification (3-way match)

A small, working prototype that checks every incoming supplier invoice against its
purchase order (Bestellung) and goods receipt (Wareneingang), decides per invoice
whether to pay, hold, escalate or block it, and routes exceptions to the right role
with a suggested action and a ready-to-send German draft - according to deterministic,
auditable rules, not an LLM's judgement.

Fictional company: **Brandt Haus & Garten GmbH** (DIY & garden retail chain, 12 stores
in Hamburg / Schleswig-Holstein).

**Deutsche Kurzfassung:** Ausgangslage, Lösung und Nutzen auf Deutsch stehen in
[Kurzfassung auf Deutsch](#kurzfassung-auf-deutsch).

Full build log and verification notes: [DESIGN_NOTES.md](DESIGN_NOTES.md).

## Demo in 60 seconds

```bash
pip install -r requirements.txt
python run_match.py
python -m pytest -q
python build_readme.py   # optional: renders this README to docs/index.html
```

Optional: run the free-text extraction with a real LLM instead of the regex
heuristic:

```bash
python run_match.py --llm
```

Config is via `.env`: `invoice_match/.env` if present, else the repo-root `.env` (the
`INVOICE_LLM_BASE_URL`/`_API_KEY`/`_MODEL` vars, falling back to `TRIAGE_LLM_*` so
piece #1's config is reused automatically) - see the repo-root `.env.example` for four
ready-made options (local Ollama, OpenRouter, Groq, Anthropic). Works with a local
model or any OpenAI-compatible endpoint; `--llm` with no config prints a clear error
and exits non-zero instead of hanging. Verified against Groq's free tier: identical 18
decisions to the heuristic path, ~67 s for the batch.

You will see: a console table with all 18 sample invoices, their supplier, gross
amount, decision and routing; a KPI block (touchless rate, money at risk caught, hours
saved, Skonto recovered); and paths to `out/report.html` - a self-contained dashboard -
plus `out/worklist.csv` (the AP team's work queue) and `out/drafts/*.txt` (German
supplier letters, ready to send).

---

## Starting situation (Ausgangslage)

A DIY/garden retail chain with a dozen stores receives around 400 supplier invoices a
month, as PDF attachments or plain email text. Today, accounts payable (Kreditorenbuchhaltung)
opens each one, keys the header and line data into the ERP by hand - a media break
(Medienbruch) - then manually looks up the matching purchase order and the goods
receipt the store recorded, and compares quantities, prices and totals line by line.
Goods receipts themselves are often booked late, because a store clerk gets to it
between customers rather than the moment the delivery truck leaves.

Roughly 80 percent of invoices are completely unremarkable, yet they get exactly the
same manual scrutiny as the other 20 percent - so the cases that actually matter (a
price quietly above the agreed rate, a short delivery, an invoice resubmitted after it
was already paid, a changed bank account) have to be caught by a tired person on
invoice number 340 of the month, not by a system built to notice them. The
consequences are concrete: missed early-payment discounts (Skonto) because nobody got
to the invoice in time, occasional overpayments that are never fully recovered, and a
real payment-diversion fraud risk if a changed IBAN on an otherwise-perfect invoice
goes unnoticed. **Affected:** accounts payable (volume and repetition), the stores and
warehouse staff (goods-receipt timeliness), category buyers (price agreements), and
ultimately the CFO, who owns the payment risk.

## Solution design (Lösungsdesign)

```
                     ┌─────────────────────┐
  *.xml (e-invoice)  │ 1a. Parse            │  xml.etree, direct - no extraction,
  ─────────────────> │     (einvoice.py)    │  no AI involved at all
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
  *.txt (free text)  │ 1b. Extract          │  regex heuristic (default, 3 layouts)
  ─────────────────> │     (extract.py)     │  or LLM (--llm); untrusted <invoice> data
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
                     │ 2. Validate           │  line totals vs. Nettobetrag,
                     │    (validate.py)      │  Nettobetrag vs. Bruttobetrag
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
                     │ 3. Match & decide     │  3-way match, duplicates, bank details -
                     │    (match.py)         │  deterministic rules from rules.toml
                     └──────────┬──────────┘
                                │
            APPROVED  /  HOLD  /  EXCEPTION  /  BLOCKED
                                │
                     ┌──────────▼──────────┐
                     │ 4. Route + draft      │  role + suggested action + Skonto
                     │    (drafts.py)        │  deadline; German letter for exceptions
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
                     │ 5. Report             │  console, results.json, worklist.csv,
                     │    (report.py)        │  report.html
                     └─────────────────────┘
```

**Why this approach.** AI is used only where the input is genuinely unstructured - reading
a free-text invoice. The payment decision itself stays fully deterministic: it is made by
plain rule functions configured in `rules.toml`, which an auditor can read end to end in a
few minutes. An LLM silently deciding which invoices get paid would not be explainable
after the fact and would be hard to reconcile with the traceability (Nachvollziehbarkeit)
that GoBD and auditors expect. Every invoice,
however it was parsed - e-invoice XML, regex or LLM - passes through the same arithmetic
plausibility check before it is matched against anything, which is exactly the guard rail
that catches a bad extraction or an LLM hallucination before it reaches a payment decision.
Structured e-invoices (XRechnung/ZUGFeRD - receiving mandatory for German B2B since 1
January 2025, issuing phased in from 2027/2028) skip the extraction step, and therefore any
AI involvement, entirely - so the AI's share of the pipeline shrinks over time by design, as
more suppliers switch to e-invoicing, not by a later decision to remove it.

**Rejected alternatives:**
- *RPA clicking through ERP screens* - brittle, breaks on every UI update, and doesn't
  transfer to a second ERP or a store using a different POS system.
- *Buy a full AP-automation suite* - the right tool once the process and the rule set
  are proven at scale, but a procurement-and-integration project should follow a short
  prototype that proves the rules and the business case with the actual business, not
  precede it.
- *LLM end-to-end (read the invoice, decide, pay)* - hard to audit, and a single
  hallucinated total would go straight into a bank transfer.

**Design choices:**
- Runtime is **stdlib only** (`tomllib`, `csv`, `json`, `xml.etree`, `urllib`,
  `dataclasses`, `decimal`) - no framework lock-in, runs anywhere Python 3.11+ runs.
- Every tolerance, routing target and KPI assumption lives in **`rules.toml`**, not in
  code - a policy change is a one-line config edit, and the report shows the exact
  numbers that were used.
- Money is **`Decimal`**, rounded `ROUND_HALF_UP` to the cent everywhere - never `float`.
- **Fail-safe defaults**: anything the rules cannot confidently approve goes to a human
  queue; any critical finding (duplicate payment, changed bank details) blocks payment
  outright, regardless of how clean everything else looks.

## Value (Nutzen)

Numbers below are from an actual run of `python run_match.py` (heuristic extraction,
`--today 2026-09-23`, 18 sample invoices). Anything under "per month/year" is an
**estimate** built from the assumptions in `rules.toml [kpi]`, listed underneath - never
measured production data.

| Metric | Batch (measured, this run) | Per month/year (estimate) |
|---|---|---|
| Invoices | 18 | 400/month *(assumption)* |
| Touchless rate | 61.1% (11 of 18 auto-approved) | - |
| **Money at risk caught** | **780,95 EUR** | - |
| &nbsp;&nbsp;- Price deviation (Preisabweichung) | 31,50 EUR | |
| &nbsp;&nbsp;- Short delivery (Mindermenge) | 55,20 EUR | |
| &nbsp;&nbsp;- Duplicate invoice (Doppelrechnung) | 249,90 EUR | |
| &nbsp;&nbsp;- Changed bank details (IBAN-Abweichung) | 434,35 EUR | |
| &nbsp;&nbsp;- Invoice arithmetic error (Rechenfehler) | 10,00 EUR | |
| Skonto captured / recovered | 156,11 EUR (this batch) | ~26.301,60 EUR/year |
| Manual steps removed per clean invoice | 9 of 9 (no human step left) | - |
| Handling time, before -> after | - | 95.6 h -> 26.7 h/month |
| Time saved | - | 68.9 h/month (~0.49 FTE) |
| Cost saved | - | ~3.100 EUR/month (~37.200 EUR/year) |

Assumptions behind the estimate column (`rules.toml [kpi]`): 400 invoices/month; 9 min
per clean invoice today; 25 min in total for each invoice that needs investigation
(~33% in this batch); 12 min per exception after automation, once pre-analysed and
drafted; 45 EUR/hour loaded AP cost; 140 productive hours per FTE per month; 40% of
Skonto-eligible invoices currently miss their discount window (estimate, not measured).

**One honest caveat:** the 18 sample invoices are a teaching set, deliberately seeded
with one case per rule so every rule has something to demonstrate - not a random
production sample. That makes the measured 61.1% touchless rate conservative for a real
batch, where most invoices would be unremarkable. A real baseline has to be measured
(see *From prototype to production*) before any of these numbers go into a business
case.

## The 18 demo cases

| # | File | Scenario | Decision | Routed to |
|---|------|----------|----------|-----------|
| 1 | inv-01-s01.txt | Clean invoice, matches PO + goods receipt exactly | APPROVED | - |
| 2 | inv-02-s02.txt | Clean invoice | APPROVED | - |
| 3 | inv-03-s03.xml | Clean, structured e-invoice (UBL XML) - extraction skipped | APPROVED | - |
| 4 | inv-04-s04.txt | Clean invoice, supplier has no Skonto terms | APPROVED | - |
| 5 | inv-05-s05.txt | Clean invoice | APPROVED | - |
| 6 | inv-06-s06.txt | Clean invoice, reduced 7% VAT (live plants) | APPROVED | - |
| 7 | inv-07-s01.xml | Clean e-invoice; Skonto deadline already passed | APPROVED | - |
| 8 | inv-08-s02.txt | Clean invoice | APPROVED | - |
| 9 | inv-09-s03.txt | Clean invoice | APPROVED | - |
| 10 | inv-10-s06.xml | Clean e-invoice, 7% VAT | APPROVED | - |
| 11 | inv-11-s01.txt | Price 0.32% above PO - inside the 1% tolerance | APPROVED *(info)* | - |
| 12 | inv-12-s05.txt | Price 6% above PO (outdated supplier price list) | EXCEPTION | Einkauf (Category Buyer) |
| 13 | inv-13-s02.txt | Short delivery: 40 invoiced, 32 received | EXCEPTION | Filiale / Wareneingang |
| 14 | inv-14-s01.txt | Goods receipt not yet posted; Skonto deadline in 1 day | HOLD | automatische Wiedervorlage |
| 15 | inv-15-s03.txt | Duplicate - already paid on 2026-08-30 | BLOCKED | Kreditorenbuchhaltung |
| 16 | inv-16-s04.txt | IBAN differs from supplier master data (fraud pattern) | BLOCKED | Finance Lead (Vier-Augen-Prinzip) |
| 17 | inv-17-s02.txt | Purchase-order reference unknown to the ERP | EXCEPTION | Kreditorenbuchhaltung |
| 18 | inv-18-s06.txt | Line totals don't add up to the stated Nettobetrag | EXCEPTION | Kreditorenbuchhaltung |

## How it works (code tour)

- `apmatch/models.py` - dataclasses shared across the pipeline (`Invoice`, `Supplier`,
  `POLine`, `GoodsReceipt`, `Finding`, `MatchResult`); money is always `Decimal`.
- `apmatch/ingest.py` - loads the CSV master data (suppliers, purchase orders, goods
  receipts, payment history) and lists the incoming invoice files.
- `apmatch/einvoice.py` - parses structured UBL e-invoice XML directly with
  `xml.etree` - no extraction step, no AI.
- `apmatch/extract.py` - turns free-text invoices into an `Invoice`: a regex heuristic
  (default, handles 3 distinct supplier layouts) or an OpenAI-compatible LLM call
  (`--llm`) - same output shape either way, same downstream validation.
- `apmatch/validate.py` - arithmetic plausibility check (line totals vs. Nettobetrag,
  Nettobetrag vs. Bruttobetrag) - run on every invoice, regardless of how it was parsed.
- `apmatch/match.py` - the rules engine: one pure function per rule (price tolerance,
  short delivery, missing goods receipt, exact/fuzzy duplicate, IBAN mismatch, unknown
  PO/supplier), the decision precedence, and the suggested payable amount.
- `apmatch/skonto.py` - early-payment discount deadline and amount, and the
  "is it still reachable" check.
- `apmatch/kpi.py` - every before/after time and cost formula, each with a docstring -
  the numbers in the report and in this README come from here.
- `apmatch/drafts.py` - template-based, fully German supplier/internal correspondence
  for exceptions (no LLM - predictable, auditable wording).
- `apmatch/formatting.py` - the one place that formats money (`1.234,56 EUR`) and dates
  (`24.09.2026`) - used by the console, the report, `worklist.csv` and every finding
  message.
- `apmatch/report.py` - console table, `results.json`, `worklist.csv`, and the
  self-contained `report.html` dashboard.
- `rules.toml` - every tolerance, routing target and KPI assumption, commented,
  business-readable.
- `tests/` - 100 pytest cases: extraction fixtures per sample file, e-invoice parsing,
  arithmetic validation, every rule in isolation, decision precedence, a full
  end-to-end run pinning all 18 decisions, and a fully-mocked LLM path (no test ever
  touches the network).

## From prototype to production (next steps)

Before any number above goes into a business case, measure the real baseline - a short
time study or a process-mining export from the ERP - and replace the `rules.toml`
assumptions with it. Integrate read-only with the ERP for purchase orders and goods
receipts instead of CSV exports, and write decisions back through its posting API
instead of a `worklist.csv` a human re-keys. Extend the simplified UBL parser to a full
EN 16931 parser covering XRechnung (UBL/CII) and ZUGFeRD, and add OCR for scanned paper
invoices that never arrive as text at all. If LLM extraction is used in production, add
a confidence score with a field-level review UI for anything below threshold, and run
it against a local or EU-hosted model for DSGVO-clean data handling. Extend the rule set for partial
deliveries, cumulative/collective invoicing, freight and surcharges, credit notes and
multi-currency - none of which the 18 demo cases cover. Tolerances need governance:
agree the price/quantity tolerance bands with Controlling rather than a developer
default, and add a four-eyes approval step above a payable-amount threshold. Add a
GoBD-compliant, immutable audit trail and archiving instead of an overwritable
`results.json`. Then pilot with two or three high-volume suppliers, track the touchless
rate and exception reasons weekly against the measured baseline, and only roll out
further once that trend is trusted.

## Kurzfassung auf Deutsch

**Ausgangslage:** Eine Baumarktkette mit zwölf Filialen in Hamburg und Schleswig-Holstein
erhält monatlich rund 400 Lieferantenrechnungen per PDF oder E-Mail. Die Kreditorenbuchhaltung
prüft jede Rechnung von Hand gegen die Bestellung im ERP-System und den Wareneingang der
Filiale, überträgt die Rechnungsdaten manuell ins System - ein klassischer Medienbruch - und
wartet nicht selten auf einen verspätet erfassten Wareneingang. Rund 80 Prozent der Rechnungen
sind dabei völlig unauffällig, werden aber genauso aufwendig geprüft wie die restlichen 20
Prozent, wodurch die eigentlich kritischen Fälle - Preisabweichungen, Mindermengen,
Doppelrechnungen, geänderte Bankverbindungen - im Tagesgeschäft untergehen. Die Folgen sind
konkret: verpasstes Skonto, gelegentliche Überzahlungen und ein reales Betrugsrisiko bei
manipulierten Kontodaten. Betroffen sind die Kreditorenbuchhaltung, die Filialen mit ihrem
Wareneingang, der Einkauf und am Ende die Geschäftsführung, die das Zahlungsrisiko trägt.

**Lösungsdesign:** Der Prototyp führt für jede eingehende Rechnung einen klassischen
Dreiwege-Abgleich (Rechnung / Bestellung / Wareneingang) durch. Strukturierte E-Rechnungen
(XRechnung/ZUGFeRD) werden direkt eingelesen, unstrukturierter Rechnungstext wird per
Regel-Heuristik oder wahlweise per LLM ausgelesen - in beiden Fällen folgt eine rein
rechnerische Plausibilitätsprüfung, die auch eine fehlerhafte KI-Extraktion abfängt, bevor
irgendetwas gegen eine Bestellung geprüft wird. Die eigentliche Zahlungsentscheidung -
freigeben, zurückstellen, an einen Menschen eskalieren oder blockieren - trifft ausschließlich
ein deterministisches, in `rules.toml` konfiguriertes Regelwerk, niemals die KI selbst: Eine
nicht nachvollziehbare KI-Entscheidung über eine Zahlung wäre kaum nachvollziehbar und schwer prüfbar.
Jede Ausnahme wird automatisch an die zuständige Rolle geroutet, inklusive
Handlungsempfehlung und einem fertigen deutschen Schreiben an den Lieferanten.

**Nutzen:** Im Testlauf mit 18 Beispielrechnungen wurden 61,1 Prozent automatisch
freigegeben, 780,95 EUR an Risikobetrag abgefangen (Preisabweichungen, eine Mindermenge,
eine Doppelrechnung, eine geänderte Bankverbindung, ein Rechenfehler) und 156,11 EUR Skonto
in diesem Batch gesichert. Hochgerechnet auf 400 Rechnungen im Monat sinkt der geschätzte
Bearbeitungsaufwand von 95,6 auf 26,7 Stunden im Monat - rund 0,49 Vollzeitstellen bzw. etwa
3.100 EUR im Monat bei 45 EUR Stundensatz -, und pro Jahr ließen sich schätzungsweise weitere
26.301,60 EUR an heute verpasstem Skonto zurückgewinnen. Alle hochgerechneten Zahlen sind
Schätzungen auf Basis der Annahmen in `rules.toml` und müssen vor einem Rollout durch eine
echte Zeiterfassung ersetzt werden; da die 18 Testfälle bewusst je einen Fall pro Regel
enthalten, ist die gemessene Automatisierungsquote von 61 Prozent als Untergrenze zu
verstehen.

**Nächste Schritte:** Vor einem Rollout steht die Messung der echten Ist-Baseline
(Zeitstudie oder Process-Mining-Auswertung aus dem ERP), eine lesende ERP-Anbindung für
Bestellungen und Wareneingänge samt Rückschreiben über die Buchungs-API statt CSV-Export,
sowie ein vollständiger EN-16931-Parser für XRechnung/ZUGFeRD und OCR für gescannte
Papierrechnungen. Beim Einsatz eines LLM sind ein Konfidenzwert mit Review-Oberfläche sowie
ein lokal oder EU-gehostetes Modell für DSGVO-konforme Verarbeitung vorzusehen; das Regelwerk
muss um Teillieferungen, Sammelrechnungen, Fracht- und Nebenkosten, Gutschriften und
Fremdwährungen erweitert werden, und die Toleranzen sind gemeinsam mit dem Controlling
festzulegen, ergänzt um ein Vier-Augen-Prinzip ab einem bestimmten Zahlbetrag. Mit einem
GoBD-konformen Audit-Log empfiehlt sich anschließend ein Pilot mit zwei bis drei
umsatzstarken Lieferanten, bei dem Automatisierungsquote und Ausnahmegründe wöchentlich
gegen die gemessene Baseline verfolgt werden, bevor der breite Rollout erfolgt.

## Project structure

```
invoice_match/
  README.md
  DESIGN_NOTES.md              # build log: deviations from spec + verification notes
  requirements.txt             # pytest only - runtime is stdlib
  rules.toml                   # tolerances, routing, KPI assumptions (commented)
  run_match.py                 # CLI entry point
  apmatch/
    __init__.py
    models.py                  # shared dataclasses
    ingest.py                  # CSV master-data loader
    einvoice.py                # structured e-invoice XML parser
    extract.py                 # heuristic + LLM free-text extraction
    validate.py                # arithmetic plausibility check
    match.py                   # rules engine + decision precedence
    skonto.py                  # early-payment discount calculation
    kpi.py                     # before/after time & cost formulas
    drafts.py                  # German exception correspondence (template-based)
    formatting.py              # shared money/date formatting
    report.py                  # console, results.json, worklist.csv, report.html
  data/
    suppliers.csv
    purchase_orders.csv
    goods_receipts.csv
    posted_invoices.csv        # payment history, for duplicate detection
    invoices/                  # 18 sample invoices: 15 x .txt, 3 x .xml (e-invoice)
  tests/                       # pytest suite, 100 tests
  out/                         # generated: report.html, results.json, worklist.csv, drafts/*.txt
```

## Limitations

- All company, supplier and invoice data is fictional, generated for this demo.
- Invoice lines are matched to purchase-order lines by exact SKU (Art.-Nr.), not fuzzy
  text matching - realistic for how ERPs actually match line items, but a line whose
  SKU isn't on the PO is silently skipped rather than raising its own finding.
- The fuzzy-duplicate rule only checks against payment history
  (`data/posted_invoices.csv`), not against other invoices arriving in the same batch.
- No partial-delivery, cumulative-invoice, freight/surcharge, credit-note or
  multi-currency handling yet (see *From prototype to production*).
