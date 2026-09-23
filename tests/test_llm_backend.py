"""Tests for the OpenAI-compatible LLM backend and provider resolution.

These run fully offline: a tiny stdlib http.server is spun up on 127.0.0.1
(an ephemeral port) in a background thread to stand in for a local/hosted
OpenAI-compatible chat-completions endpoint, so no real network access or
API key is needed.
"""
import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.models import Mail, SecurityScreen
from triage import classify as classify_mod


def make_mail(**kwargs) -> Mail:
    defaults = dict(
        id="TEST",
        from_addr="someone@example.com",
        from_name="Someone",
        to="info@nordlicht-solar.de",
        subject="Öffnungszeiten?",
        body="Wann habt ihr geöffnet?",
        received_at="2026-09-10T10:00:00+02:00",
        attachments=[],
        links=[],
    )
    defaults.update(kwargs)
    return Mail(**defaults)


def make_security(**kwargs) -> SecurityScreen:
    defaults = dict(
        risk_score=0, risk_level="none", findings=[], injection_suspected=False,
        sanitized_body="Wann habt ihr geöffnet?",
    )
    defaults.update(kwargs)
    return SecurityScreen(**defaults)


CANNED_CONTENT = json.dumps({
    "category": "faq_question",
    "confidence": 0.93,
    "summary": "Kunde fragt nach den Öffnungszeiten.",
    "language": "de",
    "sentiment": "neutral",
    "injection_suspected": False,
    "requests_nonpublic_info": False,
    "suggested_department": "Kundenservice",
})


def _chat_completion_payload(content: str) -> dict:
    return {"id": "chatcmpl-test", "choices": [{"message": {"role": "assistant", "content": content}}]}


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Test double for an OpenAI-compatible server.

    Subclasses set `responses` (a list of (status, body_dict) or
    (status, body_dict, extra_headers) tuples consumed in order, one per
    POST - the last entry repeats if there are more requests than entries)
    and start with `requests = []`, which this handler appends
    {"headers": ..., "body": ...} to for the test to inspect afterwards.
    """
    responses: list = []
    requests: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw.decode("utf-8"))
        self.__class__.requests.append({"headers": dict(self.headers), "body": body})
        idx = len(self.__class__.requests) - 1
        responses = self.__class__.responses
        entry = responses[min(idx, len(responses) - 1)]
        if len(entry) == 3:
            status, payload, extra_headers = entry
        else:
            status, payload = entry
            extra_headers = {}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in extra_headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):  # noqa: A002 - silence test server logging
        pass


def _start_server(handler_cls):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server, thread):
    server.shutdown()
    thread.join(timeout=5)


def test_openai_compatible_sends_system_and_user_messages_with_bearer_auth():
    class Handler(_RecordingHandler):
        responses = [(200, _chat_completion_payload(CANNED_CONTENT))]
        requests = []

    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        result = classify_mod.classify_with_openai_compatible(
            make_mail(), make_security(), base_url=base_url, api_key="test-key", model="qwen2.5:7b",
        )
    finally:
        _stop_server(server, thread)

    assert result.category == "faq_question"
    assert result.confidence == 0.93
    assert result.classifier.startswith("openai-compatible:")
    assert "qwen2.5:7b" in result.classifier

    assert len(Handler.requests) == 1
    req = Handler.requests[0]
    assert req["headers"].get("Authorization") == "Bearer test-key"
    sent = req["body"]
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][1]["role"] == "user"
    assert "<email>" in sent["messages"][1]["content"]
    assert sent["response_format"] == {"type": "json_object"}


def test_openai_compatible_omits_auth_header_when_api_key_empty():
    class Handler(_RecordingHandler):
        responses = [(200, _chat_completion_payload(CANNED_CONTENT))]
        requests = []

    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        result = classify_mod.classify_with_openai_compatible(
            make_mail(), make_security(), base_url=base_url, api_key="", model="qwen2.5:7b",
        )
    finally:
        _stop_server(server, thread)

    assert result.category == "faq_question"
    assert "Authorization" not in Handler.requests[0]["headers"]


def test_openai_compatible_retries_without_response_format_on_400():
    class Handler(_RecordingHandler):
        responses = [
            (400, {"error": {"message": "response_format is not supported by this model"}}),
            (200, _chat_completion_payload(CANNED_CONTENT)),
        ]
        requests = []

    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        result = classify_mod.classify_with_openai_compatible(
            make_mail(), make_security(), base_url=base_url, api_key="k", model="local-model",
        )
    finally:
        _stop_server(server, thread)

    assert result.category == "faq_question"
    assert len(Handler.requests) == 2
    assert "response_format" in Handler.requests[0]["body"]
    assert "response_format" not in Handler.requests[1]["body"]


def test_classify_falls_back_to_heuristic_on_server_error(monkeypatch):
    class Handler(_RecordingHandler):
        responses = [(500, {"error": "boom"})]
        requests = []

    monkeypatch.setattr(classify_mod, "_sleep", lambda seconds: None)
    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        monkeypatch.setenv("TRIAGE_LLM_PROVIDER", "openai")
        monkeypatch.setenv("TRIAGE_LLM_BASE_URL", base_url)
        monkeypatch.setenv("TRIAGE_LLM_MODEL", "local-model")
        monkeypatch.setenv("TRIAGE_LLM_API_KEY", "")
        result = classify_mod.classify(
            make_mail(), make_security(), use_llm=True, provider="openai",
        )
    finally:
        _stop_server(server, thread)

    assert "llm error" in result.classifier
    assert result.classifier.startswith("heuristic (llm error:")
    # the heuristic fallback still produces a real category, not an empty result
    assert result.category == "faq_question"


def test_resolve_provider_auto_prefers_openai_when_base_url_set(monkeypatch):
    monkeypatch.delenv("TRIAGE_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("TRIAGE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("TRIAGE_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("TRIAGE_LLM_API_KEY", "ollama")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-ignored")

    provider, settings = classify_mod.resolve_provider(None)

    assert provider == "openai"
    assert settings == {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "qwen2.5:7b",
    }


def test_resolve_provider_auto_falls_back_to_anthropic_without_base_url(monkeypatch):
    monkeypatch.delenv("TRIAGE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("TRIAGE_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("TRIAGE_MODEL", raising=False)

    provider, settings = classify_mod.resolve_provider(None)

    assert provider == "anthropic"
    assert settings["model"] == classify_mod.DEFAULT_MODEL


def test_resolve_provider_auto_falls_back_to_heuristic_with_nothing_configured(monkeypatch):
    monkeypatch.delenv("TRIAGE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("TRIAGE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    provider, settings = classify_mod.resolve_provider(None)

    assert provider == "heuristic"
    assert settings == {}


def test_resolve_provider_explicit_openai_without_model_raises(monkeypatch):
    monkeypatch.setenv("TRIAGE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("TRIAGE_LLM_MODEL", raising=False)

    with pytest.raises(ValueError):
        classify_mod.resolve_provider("openai")


def test_resolve_provider_explicit_heuristic_ignores_everything_else(monkeypatch):
    monkeypatch.setenv("TRIAGE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("TRIAGE_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    provider, settings = classify_mod.resolve_provider("heuristic")

    assert provider == "heuristic"
    assert settings == {}


# --- retry / rate-limit / error-message handling (Groq live-testing fixes) ----


@pytest.mark.parametrize(
    "text, expected",
    [
        ("12", 12.0),
        ("38.812s", 38.812),
        ("38.812", 38.812),
        ("20m9.6s", 1209.6),
        ("1h2m3s", 3723.0),
        ("2m", 120.0),
        ("garbage", None),
    ],
)
def test_parse_duration(text, expected):
    result = classify_mod._parse_duration(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_retry_wait_seconds_prefers_retry_after():
    headers = {"Retry-After": "5", "x-ratelimit-reset-tokens": "100s"}
    assert classify_mod._retry_wait_seconds(headers, attempt=1) == 5.0


def test_retry_wait_seconds_uses_reset_tokens_when_no_retry_after():
    headers = {"x-ratelimit-reset-tokens": "38.812s"}
    assert classify_mod._retry_wait_seconds(headers, attempt=1) == pytest.approx(38.812)


def test_retry_wait_seconds_caps_at_max_wait():
    headers = {"Retry-After": "9999"}
    assert classify_mod._retry_wait_seconds(headers, attempt=1) == classify_mod.LLM_MAX_WAIT_SECONDS


def test_retry_after_429_then_success_records_wait_and_two_requests(monkeypatch):
    waits = []
    monkeypatch.setattr(classify_mod, "_sleep", lambda seconds: waits.append(seconds))

    class Handler(_RecordingHandler):
        responses = [
            (429, {"error": {"message": "rate limited"}}, {"Retry-After": "3"}),
            (200, _chat_completion_payload(CANNED_CONTENT)),
        ]
        requests = []

    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        result = classify_mod.classify_with_openai_compatible(
            make_mail(), make_security(), base_url=base_url, api_key="k", model="local-model",
        )
    finally:
        _stop_server(server, thread)

    assert result.category == "faq_question"
    assert waits == [3.0]
    assert len(Handler.requests) == 2


def test_persistent_429_raises_llm_request_error_after_max_attempts(monkeypatch):
    waits = []
    monkeypatch.setattr(classify_mod, "_sleep", lambda seconds: waits.append(seconds))

    class Handler(_RecordingHandler):
        responses = [(429, {"error": {"message": "rate limited"}})]
        requests = []

    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        with pytest.raises(classify_mod.LLMRequestError) as excinfo:
            classify_mod.classify_with_openai_compatible(
                make_mail(), make_security(), base_url=base_url, api_key="k", model="local-model",
            )
        assert "HTTP 429" in str(excinfo.value)
        assert len(Handler.requests) == classify_mod.LLM_MAX_ATTEMPTS

        Handler.requests = []
        monkeypatch.setenv("TRIAGE_LLM_PROVIDER", "openai")
        monkeypatch.setenv("TRIAGE_LLM_BASE_URL", base_url)
        monkeypatch.setenv("TRIAGE_LLM_MODEL", "local-model")
        monkeypatch.setenv("TRIAGE_LLM_API_KEY", "k")
        result = classify_mod.classify(
            make_mail(), make_security(), use_llm=True, provider="openai",
        )
    finally:
        _stop_server(server, thread)

    assert result.classifier.startswith("heuristic (llm error:")
    assert "HTTP 429" in result.classifier


def test_400_json_validate_failed_recovers_from_failed_generation(monkeypatch):
    monkeypatch.setattr(classify_mod, "_sleep", lambda seconds: None)

    class Handler(_RecordingHandler):
        responses = [
            (400, {
                "error": {
                    "message": "Failed to generate JSON. Please adjust your prompt.",
                    "type": "invalid_request_error",
                    "code": "json_validate_failed",
                    "failed_generation": CANNED_CONTENT,
                }
            }),
        ]
        requests = []

    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        result = classify_mod.classify_with_openai_compatible(
            make_mail(), make_security(), base_url=base_url, api_key="k", model="local-model",
        )
    finally:
        _stop_server(server, thread)

    assert result.category == "faq_question"
    assert len(Handler.requests) == 1


def test_400_json_validate_failed_with_unusable_generation_retries_without_response_format(monkeypatch):
    monkeypatch.setattr(classify_mod, "_sleep", lambda seconds: None)

    class Handler(_RecordingHandler):
        responses = [
            (400, {
                "error": {
                    "message": "Failed to generate JSON. Please adjust your prompt.",
                    "type": "invalid_request_error",
                    "code": "json_validate_failed",
                    "failed_generation": "not valid json at all, just rambling reasoning text",
                }
            }),
            (200, _chat_completion_payload(CANNED_CONTENT)),
        ]
        requests = []

    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        result = classify_mod.classify_with_openai_compatible(
            make_mail(), make_security(), base_url=base_url, api_key="k", model="local-model",
        )
    finally:
        _stop_server(server, thread)

    assert result.category == "faq_question"
    assert len(Handler.requests) == 2
    assert "response_format" in Handler.requests[0]["body"]
    assert "response_format" not in Handler.requests[1]["body"]


def test_404_error_message_includes_provider_text(monkeypatch):
    monkeypatch.setattr(classify_mod, "_sleep", lambda seconds: None)

    class Handler(_RecordingHandler):
        responses = [
            (404, {
                "error": {
                    "message": "The model `bad-model` does not exist or you do not have access to it",
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                }
            }),
        ]
        requests = []

    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        with pytest.raises(classify_mod.LLMRequestError) as excinfo:
            classify_mod.classify_with_openai_compatible(
                make_mail(), make_security(), base_url=base_url, api_key="k", model="bad-model",
            )
    finally:
        _stop_server(server, thread)

    assert "does not exist" in str(excinfo.value)


def test_request_sends_mail_triage_user_agent():
    class Handler(_RecordingHandler):
        responses = [(200, _chat_completion_payload(CANNED_CONTENT))]
        requests = []

    server, thread = _start_server(Handler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        classify_mod.classify_with_openai_compatible(
            make_mail(), make_security(), base_url=base_url, api_key="k", model="local-model",
        )
    finally:
        _stop_server(server, thread)

    assert Handler.requests[0]["headers"].get("User-Agent", "").startswith("mail-triage-agent")
