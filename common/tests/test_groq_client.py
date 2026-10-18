"""The temporary Groq fallback adapter (no network: mocked transport)."""

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
