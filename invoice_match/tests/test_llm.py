"""LLM extraction path, fully mocked - no test here ever touches the
network. Covers: valid JSON response, invalid JSON -> heuristic fallback,
and the clear-error/non-zero-exit behaviour when --llm has no config."""
import json
import os
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from apmatch import extract

SAMPLE_TEXT = (Path(__file__).resolve().parent.parent / "data" / "invoices" / "inv-01-s01.txt").read_text(encoding="utf-8")


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _chat_payload(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_extract_llm_valid_json():
    valid_json = json.dumps({
        "invoice_number": "RE-TEST-1", "invoice_date": "2026-09-15", "po_number": "PO-2026-0401",
        "iban": "DE12500105170648512345", "supplier_vat_id": "DE118743205", "store": "HH1",
        "net_eur": 443.50, "vat_rate_pct": 19, "vat_amount_eur": 84.27, "gross_eur": 527.77,
        "lines": [{"sku": "GN-ERDE20", "description": "Blumenerde 20L", "qty": 40,
                   "unit_price_eur": 4.20, "line_total_eur": 168.00}],
    })
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_chat_payload(valid_json))):
        invoice = extract.extract_llm(SAMPLE_TEXT, "inv-01-s01.txt", "https://fake.local/v1", "key", "fake-model")

    assert invoice.extraction_source == "llm"
    assert invoice.invoice_number == "RE-TEST-1"
    assert invoice.net_eur == Decimal("443.50")
    assert len(invoice.lines) == 1
    assert "LLM extraction failed" not in invoice.extraction_note


def test_extract_llm_invalid_json_falls_back_to_heuristic():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_chat_payload("not json at all"))):
        invoice = extract.extract_llm(SAMPLE_TEXT, "inv-01-s01.txt", "https://fake.local/v1", "key", "fake-model")

    # Falls back to the heuristic extractor, which parses this real sample fine.
    assert invoice.extraction_source == "heuristic"
    assert invoice.invoice_number == "RE-2026-6001"
    assert "LLM extraction failed" in invoice.extraction_note


def test_extract_llm_network_error_falls_back_to_heuristic():
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no network in tests")):
        invoice = extract.extract_llm(SAMPLE_TEXT, "inv-01-s01.txt", "https://fake.local/v1", "key", "fake-model")
    assert invoice.extraction_source == "heuristic"
    assert "LLM extraction failed" in invoice.extraction_note


def test_resolve_llm_config_raises_clear_error_without_env(monkeypatch):
    for var in ("INVOICE_LLM_BASE_URL", "INVOICE_LLM_MODEL", "TRIAGE_LLM_BASE_URL", "TRIAGE_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(extract.LLMConfigError, match="INVOICE_LLM_BASE_URL"):
        extract.resolve_llm_config()


def test_resolve_llm_config_reads_env(monkeypatch):
    monkeypatch.setenv("INVOICE_LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("INVOICE_LLM_MODEL", "some-model")
    monkeypatch.setenv("INVOICE_LLM_API_KEY", "secret")
    base_url, api_key, model = extract.resolve_llm_config()
    assert base_url == "https://api.example.com/v1"
    assert model == "some-model"
    assert api_key == "secret"


def test_resolve_llm_config_falls_back_to_triage_vars(monkeypatch):
    monkeypatch.delenv("INVOICE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("INVOICE_LLM_MODEL", raising=False)
    monkeypatch.setenv("TRIAGE_LLM_BASE_URL", "https://triage.example.com/v1")
    monkeypatch.setenv("TRIAGE_LLM_MODEL", "triage-model")
    base_url, api_key, model = extract.resolve_llm_config()
    assert base_url == "https://triage.example.com/v1"
    assert model == "triage-model"


def test_load_dotenv_does_not_override_existing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOICE_LLM_MODEL", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text("INVOICE_LLM_MODEL=from-dotenv\nINVOICE_LLM_BASE_URL=https://from-dotenv/v1\n",
                         encoding="utf-8")
    extract.load_dotenv_files(tmp_path)
    assert os.environ["INVOICE_LLM_MODEL"] == "already-set"
    assert os.environ["INVOICE_LLM_BASE_URL"] == "https://from-dotenv/v1"
