"""apmatch - accounts-payable 3-way match engine for Brandt Haus & Garten GmbH.

Pipeline (see run_match.py): ingest master data -> parse each invoice
(structured e-invoice XML parsed directly, free-text invoices extracted by
regex heuristic or optionally an LLM) -> validate arithmetic plausibility ->
match against PO + goods receipt and apply deterministic rules -> decide
APPROVED / EXCEPTION / HOLD / BLOCKED -> report.

Design principle: AI (when used at all) only touches the unstructured edge
(reading invoice text). The approve/reject decision itself is made by
plain, auditable rule functions in match.py, configured from rules.toml -
never by a model. That split is what makes the decisions explainable to an
auditor and reproducible in tests.
"""
