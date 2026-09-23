# Design notes - Invoice Match (Rechnungsprüfung / 3-way match)

Implementation notes for the lead engineer's spec. Not the README (a later
step writes that) - this is deviations + the exact numbers the demo prints,
so a reviewer can check the build against the spec quickly.

## Deviations from the spec

1. **Finding gets an extra `hold: bool` field beyond `severity`.** The spec
   lists `severity (info|warning|critical)` and separately says "any
   HOLD-type finding -> HOLD". Those don't quite fit in one field, so
   `GR_NOT_POSTED` carries `severity="warning"` (for display, e.g. in
   worklist.csv it still reads sensibly) plus `hold=True`, and
   `match.decide()` checks `hold` before `severity=="warning"` in its
   precedence chain. Documented in `models.py`.

2. **PO/invoice-line matching is by SKU, not fuzzy text matching.** Every
   invoice line carries an `Art.-Nr.`/SKU (added to all three text layouts
   and the XML), and `match.py` matches invoice lines to PO lines by exact
   SKU within the same PO. This is how real ERPs do it and keeps the
   heuristic extractor's job (regex, not NLP) realistic - but it is a
   simplification versus fuzzy description matching, which I did not
   implement. An invoice line whose SKU isn't on the PO is silently
   skipped by the price/qty rules rather than raising its own "line not on
   PO" finding - out of scope for the 18 sample scenarios, worth flagging
   as a real gap if this went to production.

3. **Money-at-risk KPI is a rule_id whitelist**, not "every finding's
   `amount_impact_eur`". The spec names exactly five categories (price
   deviation, short-delivery, duplicate, IBAN-block, arithmetic error); a
   `PO_UNKNOWN`/`SUPPLIER_UNKNOWN` finding still carries useful context in
   its own `amount_impact_eur` for the worklist, but is deliberately
   excluded from the headline "money at risk caught" sum in `kpi.py`
   (`MONEY_AT_RISK_RULES` in `match.py`) - those are process gaps, not a
   quantified financial exposure in the same sense.

4. **Duplicate exact + fuzzy are merged into one "DUPLICATE" bucket** in
   the money-at-risk breakdown and the drafted letter, since they're the
   same underlying AP scenario ("don't pay this twice") at two confidence
   levels.

5. **No skonto deadline/pay-by shown for BLOCKED invoices.** `skonto.py`
   would happily compute a deadline for a blocked invoice (it's just date
   arithmetic on `invoice_date`), but displaying "pay by X for a discount"
   next to a payment that is blocked outright is misleading, so
   `match.match_invoice()` forces `skonto_eur/deadline/pay_by` to
   `0.00/None/None` whenever `decision == "BLOCKED"`.

6. **Fuzzy duplicate is checked only against `posted_invoices.csv`**, not
   against other invoices in the same batch. The spec's wording ("same
   supplier, same gross amount, invoice date within N days, different
   invoice number") reads naturally either way; checking against payment
   history matches how the exact-duplicate rule works and is what the unit
   tests in `tests/test_match_rules.py` exercise with hand-built
   `PostedInvoice` objects, per the spec's own instruction that this rule
   needs no sample invoice to trigger it.

7. **`--llm` was smoke-tested against the real Groq endpoint once**, using
   the `TRIAGE_LLM_*` credentials already present in the repo-root `.env`
   (loaded automatically by the `INVOICE_LLM_*` -> `TRIAGE_LLM_*` fallback
   the spec asked for). All 15 free-text invoices extracted correctly and
   produced the identical 18 decisions as the heuristic path. I did not
   repeat this to avoid unnecessary API usage on your key; `out/` in this
   delivery reflects the default heuristic run. The mocked tests in
   `tests/test_llm.py` (valid JSON, invalid JSON -> fallback, network
   error -> fallback, missing config -> `LLMConfigError`) never touch the
   network, per the spec's instruction.

Nothing outside `invoice_match/` was modified, and `.env` was never read
in a way that printed or copied its contents (only existence/consumption
via the standard env-var loading path in `extract.load_dotenv_files`).

## Review round 2 - fixes applied

The lead engineer's review requested 7 fixes after the first pass. All 7
are in; summary of what changed and where:

1. **KPI "before" formula corrected** (`apmatch/kpi.py:extrapolate`).
   `exception_minutes_manual` (defined in `rules.toml`, previously unused)
   now drives `hours_before` via `human_share = (exceptions+blocked)/total`:
   touchless-share invoices still cost `manual_minutes_per_invoice` today,
   the human-share cost the heavier `exception_minutes_manual` on top.
   `hours_after` uses `exception_minutes_assisted` for the human share only.
   Batch result: **before 95.6 h -> after 26.7 h, saved 68.9 h/month
   (~0.49 FTE)**, matching the review's expected figures exactly. The
   before/after report section now prints "95.6 h today -> 26.7 h after
   automation" so the calculation is visible, not just the saved number.

2. **Supplier drafts are fully German.** Added a structured `Finding.details`
   dict (`apmatch/models.py`) populated by every rule in `match.py`/
   `validate.py`; `apmatch/drafts.py` was rewritten to build every letter
   from `details` + `formatting.fmt_eur`/`fmt_de_date` and never interpolates
   `finding.message` (which stays English, report-facing) into a draft.

3. **One shared money/date formatter** (`apmatch/formatting.py`, new module):
   `fmt_eur` and `fmt_de_date` are now used by console, report.html,
   worklist.csv, `match.py`/`validate.py` finding messages, and drafts.
   `results.json` is the one deliberate exception (plain decimal strings /
   ISO dates, for machine consumption).

4. **Rule labels** (`match.RULE_LABELS` / `match.rule_label`): English label
   with the German term in parentheses (e.g. "Price deviation
   (Preisabweichung)"). Used in the money-at-risk breakdown and the "how
   decisions are made" table, which now has three columns: label, `rule_id`
   (monospace), routed-to.

5. **Report layout**: `.nowrap` on file-name/gross-amount cells (right-
   aligned), and the exception worklist is now grouped in two levels -
   severity tier first (BLOCKED -> EXCEPTION -> HOLD), route within each
   tier second - instead of one flat alphabetical-by-route list. This
   matters concretely for "Kreditorenbuchhaltung", which has both a BLOCKED
   invoice (15) and EXCEPTIONs (17, 18): they now land in different tiers
   rather than one mixed group.

6. **Skonto urgency, computed not hardcoded** (`match.rule_gr_missing`, now
   takes `supplier`/`today`): for a HOLD whose skonto deadline is within
   `[hold].skonto_risk_days`, the finding message spells out the deadline,
   days left, and EUR at risk via `skonto.compute_skonto` on the invoice's
   own gross and the supplier's real terms. inv-14 -> "Skonto deadline
   24.09.2026 (in 1 day) - 9,29 EUR discount at risk if the goods receipt
   is not posted by then." Report worklist cards use "today" instead of a
   same-day date (inv-12's deadline is 2026-09-23, shown as "today").

7. **Console table**: Route column width is now computed from the longest
   route string actually in the batch (fits "Finance Lead
   (Vier-Augen-Prinzip)" without truncation); APPROVED rows show
   `- (auto-posted)` instead of the truncated "Automatisch verbucht (kein
   Revi...".

None of these required touching the sample data or the intended 18
decisions - all still APPROVED/EXCEPTION/HOLD/BLOCKED exactly as before.

## Final numbers (heuristic extraction, `--today 2026-09-23`, default run)

```
Invoices processed:            18
Touchless (APPROVED):          11 (61.1%)
Exceptions / Holds / Blocked:  4 / 1 / 2
Money at risk caught:          780,95 EUR
  Price deviation (Preisabweichung):        31,50 EUR
  Short delivery (Mindermenge):             55,20 EUR
  Duplicate invoice (Doppelrechnung):      249,90 EUR
  Changed bank details (IBAN-Abweichung):  434,35 EUR
  Invoice arithmetic error (Rechenfehler):  10,00 EUR
Skonto captured (batch):       156,11 EUR
Est. hours/month:              95.6 h -> 26.7 h (saved 68.9 h, ~0.49 FTE,
                                ~3.100,00 EUR/month, ~37.200,00 EUR/year)
Est. skonto recovered / year:  26.301,60 EUR
```

Per-invoice decisions (all 18 match the spec's demo script exactly, and
`tests/test_end_to_end.py` pins every one of them):

| # | file | decision | route |
|---|------|----------|-------|
| 1-10 | inv-01..inv-10 | APPROVED | - |
| 11 | inv-11-s01.txt | APPROVED (info: price within tolerance) | - |
| 12 | inv-12-s05.txt | EXCEPTION | Einkauf (Category Buyer) |
| 13 | inv-13-s02.txt | EXCEPTION | Filiale / Wareneingang |
| 14 | inv-14-s01.txt | HOLD | automatische Wiedervorlage |
| 15 | inv-15-s03.txt | BLOCKED | Kreditorenbuchhaltung |
| 16 | inv-16-s04.txt | BLOCKED | Finance Lead (Vier-Augen-Prinzip) |
| 17 | inv-17-s02.txt | EXCEPTION | Kreditorenbuchhaltung |
| 18 | inv-18-s06.txt | EXCEPTION | Kreditorenbuchhaltung |

## Verification performed

- `python -m pytest -q` from `invoice_match/`: **100 passed** (96 from the
  first pass + 4 new: skonto-urgency-note computation, "no urgency when
  deadline is far away", "today" wording, and German-detail-line drafts).
- `python run_match.py` from `invoice_match/`: runs end-to-end, prints the
  console table + KPI block, writes `out/report.html`, `out/results.json`,
  `out/worklist.csv`, `out/drafts/*.txt`.
- `python invoice_match/run_match.py` from the repo root: same result
  (paths resolve relative to the script, not the cwd).
- `out/report.html` re-checked in-browser after the fixes: severity-ordered
  worklist tiers (BLOCKED -> EXCEPTION -> HOLD) confirmed, inv-12's skonto
  deadline shows "today", inv-14's HOLD card and console reason show the
  computed skonto-at-risk sentence, the rules table shows label/rule_id/
  routed-to, money-at-risk breakdown shows labels, and gross-amount/file-
  name cells are right-aligned and non-wrapping (verified via computed
  CSS: `textAlign: right`, `whiteSpace: nowrap`). All 7 sections still
  render; wide tables still scroll within their own container without
  breaking page layout.
- `out/drafts/*.txt` manually inspected: all fully German (dd.mm.yyyy
  dates, "1.234,56 EUR" amounts), grepped for stray English finding
  vocabulary ("the/and/invoice/vs/within tolerance/...") - zero matches.

## Redesign - shared design system with piece #1 (Hallmark)

`out/report.html` and the new `docs/index.html` were rebuilt onto the exact
same design system as portfolio piece #1 (Mail Triage Agent -
`triage/report.py` -> `out/report.html`): same `tokens.css`, same Google
Fonts request (`FONTS_HREF`, imported from `triage.report` so there is one
source of truth), and the same CSS class vocabulary (nav/hero/flow/stats/
bars/dark band/table-filter/foot-line) reused as closely as the content
allowed. Report UI is German throughout (rule labels, reasons, actions,
drafts); console output and this file stay English.

**Stamp** (top of `REPORT_CSS` in `apmatch/report.py`):
```
/* Hallmark · genre: modern-minimal · macrostructure: Stat-Led · theme: Cobalt · enrichment: none · nav: N9 · footer: Ft2 · tone: technical · anchor hue: 256 · audience: recruiter · use: understand the run in 60 s
 * · shared system with Mail Triage report (user-directed - sibling portfolio pieces, diversification rule suspended per SKILL.md §2 design.md precedent)
 * · contrast: pass (40-41) · slop: pass (42-45) · honest: pass (46) · chrome: pass (47) · tokens: pass (48) · responsive: pass (49) · icons: pass (30) · mobile: pass (34, 49, 50-57) */
/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V3 (Variety intentionally suspended - locked to piece #1's system by user directive, not a quality gap) */
```

**Deliberate diversification suspension.** Hallmark normally requires each
new page to differ structurally from the last one built in the project
(gates 8, 20-21, 32). That rule is switched off here on purpose: sibling
portfolio pieces are meant to read as one system to a recruiter, the same
exception Hallmark itself makes for a `design.md`-managed multi-page app.
This is recorded in the stamp rather than silently ignored, and
`.hallmark/log.json` carries a matching entry.

**What changed:**
- Section order follows the piece-#1 critique's own fix (strongest
  evidence high): hero -> `#nutzen` -> `#gestoppt` -> `#arbeitsliste` ->
  `#funktionsweise` (dark band) -> `#rechnungen` -> `#annahmen`.
- Hero headline is the computed handling-time reduction (`-72%`, from
  `kpi.extrapolate`'s own before/after hours - never hardcoded), with the
  "geschätzt" marker linking to `#annahmen` right at the number.
- Decision flow bar (Freigegeben/Wiedervorlage/Klärung/Gestoppt) carries
  an aria-label with counts plus per-segment visually-hidden text.
- The pipeline-band copy for the extraction step depends on the run's
  actual `extraction_mode` (heuristic vs. LLM), mirroring piece #1's own
  P1 critique fix - pinned by `tests/test_report.py`.
- Skonto deadlines get distinct, non-colour-only urgency styling
  (`.deadline--today` / `.deadline--tomorrow`), using one new token,
  `--color-urgent`, added inside the report's own `<style>` (not to the
  shared, locked `tokens.css`).
- Found and fixed two real bugs while verifying at 320/375/414/768/1280px:
  the rule-list and assumptions tables weren't wrapped in `.table-scroll`
  and were invisibly clipped (not scrollable) below ~800px - fixed by
  wrapping both; white text on the new "Freigegeben" (green) flow-bar
  segment measured 3.58:1 contrast (needs 4.5:1) - fixed by switching to
  ink-coloured text and sizing that number into WCAG's large-text tier.
- New `invoice_match/build_readme.py` renders this README onto the same
  sidebar-TOC/prose layout as piece #1's `docs/index.html`, importing
  `DOC_CSS`/`DOC_JS`/`TOC_SYNC_JS`/`_wrap_tables`/`_rewrite_relative_links`
  from the root `build_readme.py` rather than duplicating them.
- Polish pass after lead review: every decimal/percent in the report UI
  now goes through `formatting.fmt_de_number`/`fmt_pct` (German comma,
  e.g. "6,0 %" not "6.0 %"); the hero/nav date is `23.09.2026` not
  `2026-09-23`; the `#gestoppt` intro no longer repeats "unabhängig"; the
  "N of 9 manual steps removed" claim is now computed once via
  `kpi.clean_invoice_step_counts()` (always 9 of 9 - the report and this
  file previously disagreed, "9 of 9" vs "8 of 9"); and the "Alle
  Rechnungen" table shows a short muted "automatisch verbucht" instead of
  repeating the full routing string on all 11 APPROVED rows.
