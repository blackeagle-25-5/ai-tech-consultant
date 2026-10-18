"""The Groq provider adapter (no network: mocked transport)."""

import json

import httpx
import pytest
from pydantic import BaseModel, Field

import common.llm as llm_mod
from common.groq_client import GroqAnthropicAdapter, GroqError, strict_schema
from common.llm import HAIKU, LLM, LLMError, SONNET


class Answer(BaseModel):
    status: str
    hours: float = Field(ge=0)
    tags: list[str] = []
    note: str | None = None


def adapter(handler) -> GroqAnthropicAdapter:
    return GroqAnthropicAdapter(api_key="test", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def ok(content: dict, prompt_tokens: int = 50, completion_tokens: int = 10) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)},
                                                  "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}})


@pytest.fixture(autouse=True)
def groq_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setattr(llm_mod, "LEDGER_PATH", tmp_path / "ledger.jsonl")


def test_strict_schema_requires_all_fields_and_strips_validation_keywords():
    s = strict_schema(Answer.model_json_schema())
    assert s["additionalProperties"] is False
    assert set(s["required"]) == {"status", "hours", "tags", "note"}
    assert "minimum" not in json.dumps(s) and '"default"' not in json.dumps(s)


def test_roles_map_to_groq_models_and_cost_is_recorded_as_free():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        return ok({"status": "answered", "hours": 2, "tags": [], "note": None})

    llm = LLM(client=adapter(handler))
    llm.provider = "groq"  # injected client: opt into the Groq mapping explicitly
    small = llm.structured(schema=Answer, system="s", user="q", model=HAIKU, effort="low")
    big = llm.structured(schema=Answer, system="s", user="q", model=SONNET)
    assert seen[0]["model"] == "openai/gpt-oss-20b" and seen[1]["model"] == "openai/gpt-oss-120b"
    assert seen[0]["reasoning_effort"] == "low"
    assert seen[0]["response_format"]["json_schema"]["strict"] is True
    assert small.record.model == "openai/gpt-oss-20b" and small.record.cost_usd == 0.0
    assert big.value.status == "answered"


def test_images_go_to_the_vision_model_as_data_urls():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return ok({"status": "x", "hours": 0, "tags": [], "note": None})

    llm = LLM(client=adapter(handler))
    llm.provider = "groq"
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
               {"type": "text", "text": "describe"}]
    llm.structured(schema=Answer, system="s", user=content, model=HAIKU)
    parts = seen[0]["messages"][1]["content"]
    assert seen[0]["model"] == "qwen/qwen3.8-27b"
    assert parts[0] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}


def test_rate_limit_is_retried_using_retry_after():
    waits = []
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={"error": "slow down"})
        return ok({"status": "ok", "hours": 1, "tags": [], "note": None})

    a = GroqAnthropicAdapter(api_key="t", transport=httpx.MockTransport(handler), sleep=waits.append)
    llm = LLM(client=a)
    llm.provider = "groq"
    assert llm.structured(schema=Answer, system="s", user="q").value.status == "ok"
    assert waits == [3.0]


def test_invalid_json_is_retried_then_raises():
    llm = LLM(client=adapter(lambda req: ok({"wrong": True})))
    llm.provider = "groq"
    with pytest.raises(LLMError):
        llm.structured(schema=Answer, system="s", user="q")


def test_client_error_is_not_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad schema"})

    llm = LLM(client=adapter(handler))
    llm.provider = "groq"
    with pytest.raises(LLMError):
        llm.structured(schema=Answer, system="s", user="q", schema_retries=0)
    assert calls["n"] == 1


def test_provider_selection(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_mod.provider() == "groq"  # only a Groq key present
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert llm_mod.provider() == "anthropic"  # Claude wins whenever it is available
    assert llm_mod.model_label(HAIKU) == HAIKU
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    assert llm_mod.model_label(HAIKU) == "groq:openai/gpt-oss-20b"


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(GroqError):
        GroqAnthropicAdapter()


def test_json_validation_400_is_retried_by_the_wrapper():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, json={"error": {"message": "Failed to validate JSON. Please adjust your prompt.",
                                                       "code": "json_validate_failed"}})
        return ok({"status": "ok", "hours": 1, "tags": [], "note": None})

    llm = LLM(client=adapter(handler))
    llm.provider = "groq"
    r = llm.structured(schema=Answer, system="s", user="q")
    assert r.value.status == "ok" and calls["n"] == 2 and r.record.attempts == 2


def test_long_retry_after_fails_fast_with_a_clear_message():
    a = GroqAnthropicAdapter(api_key="t", sleep=lambda s: None, transport=httpx.MockTransport(
        lambda req: httpx.Response(429, headers={"retry-after": "3600"}, json={"error": "daily limit"})))
    with pytest.raises(GroqError, match="retry-after 3600s"):
        a._post({"model": "x"})


def test_network_outage_is_retried_then_reported():
    waits = []

    def handler(req):
        raise httpx.ConnectError("getaddrinfo failed")

    a = GroqAnthropicAdapter(api_key="t", sleep=waits.append, transport=httpx.MockTransport(handler))
    with pytest.raises(GroqError, match="network error"):
        a._post({"model": "x"})
    assert waits == [1, 2, 4, 8, 16]


def test_combine_reruns_replaces_only_infrastructure_failures():
    from common.evals import EvalRow, combine_reruns

    def row(cid, passed, reason=""):
        return EvalRow("inbox", cid, "m", "x", "y", float(passed), 1, 0.0, passed, reason)

    base = [row("A", False, "GroqError: getaddrinfo failed"), row("B", False, "category how_to != complaint"), row("C", True)]
    out, n = combine_reruns(base, [row("A", True), row("B", True)])
    assert n == 1 and [r.passed for r in out] == [True, False, True]  # genuine failure B is kept


def test_quota_exhaustion_blocks_further_requests_until_retry_after():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "900"}, json={"error": "tokens per day"})

    from common.groq_client import GroqQuotaExhausted
    a = GroqAnthropicAdapter(api_key="t", sleep=lambda s: None, transport=httpx.MockTransport(handler))
    with pytest.raises(GroqQuotaExhausted):
        a._post({"model": "m"})
    with pytest.raises(GroqQuotaExhausted, match="no request sent"):
        a._post({"model": "m"})
    assert calls["n"] == 1


def test_quota_guard_stops_after_three_in_a_row():
    from common.evals import QuotaGuard

    g = QuotaGuard()
    assert not g.check("GroqQuotaExhausted: quota exhausted") and not g.check("category wrong")
    assert [g.check("quota exhausted") for _ in range(3)] == [False, False, True]


def test_short_rate_limits_keep_retrying_until_the_time_budget():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 9:
            return httpx.Response(429, headers={"retry-after": "30"}, json={"error": "tpm"})
        return ok({"status": "ok", "hours": 1, "tags": [], "note": None})

    a = GroqAnthropicAdapter(api_key="t", sleep=lambda s: None, transport=httpx.MockTransport(handler))
    assert a._post({"model": "m"})["choices"]  # 8 x 30 s = 240 s < 300 s budget: must not give up after 6
    assert calls["n"] == 9
