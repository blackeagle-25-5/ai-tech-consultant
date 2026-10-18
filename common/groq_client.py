"""Groq provider: runs the project on Groq's free tier (chosen because no Claude budget was available).

The brief asks for Claude at the centre; Claude remains supported (set ANTHROPIC_API_KEY). This adapter exposes the same `client.messages.parse(...)` shape the Anthropic SDK has, so `common.llm.LLM`
works unchanged. It talks to Groq's OpenAI-compatible HTTP API with httpx.

Docs checked 2026-10-18: console.groq.com/docs/models, /structured-outputs, /vision, /rate-limits.
"""

from __future__ import annotations

import copy
import json
import os
import time
from types import SimpleNamespace
from typing import Any

import httpx
from pydantic import ValidationError

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
STRIP_KEYS = {"default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
              "pattern", "format", "minItems", "maxItems"}
MAX_RATE_LIMIT_WAIT_S = float(os.getenv("GROQ_MAX_WAIT_S", "300"))  # free tier asks for 2-3 min waits


class GroqError(RuntimeError):
    pass


def strict_schema(schema: dict) -> dict:
    """Make a Pydantic JSON schema acceptable to Groq strict mode: every object lists all properties as
    required and sets additionalProperties false; validation-only keywords are removed (Pydantic still
    validates them on our side)."""
    s = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in STRIP_KEYS & node.keys():
                node.pop(key)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(s)
    return s


def _convert_content(content: list[dict]) -> list[dict] | str:
    parts: list[dict] = []
    for block in content:
        if block["type"] == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif block["type"] == "image":
            src = block["source"]
            url = src["url"] if src["type"] == "url" else f"data:{src['media_type']};base64,{src['data']}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
    if all(p["type"] == "text" for p in parts):
        return "\n\n".join(p["text"] for p in parts)
    return parts


class GroqAnthropicAdapter:
    def __init__(self, api_key: str | None = None, timeout_s: float = 90.0,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep) -> None:
        key = api_key or os.getenv("GROQ_API_KEY", "")
        if not key:
            raise GroqError("GROQ_API_KEY is not set.")
        self.http = httpx.Client(headers={"Authorization": f"Bearer {key}"}, timeout=timeout_s, transport=transport)
        self.sleep = sleep
        self.messages = SimpleNamespace(parse=self._parse)

    def _post(self, payload: dict) -> dict | None:
        """Return the response JSON, or None when Groq rejected the generated JSON (caller retries)."""
        waited = 0.0
        last = ""
        for attempt in range(6):
            try:
                r = self.http.post(GROQ_URL, json=payload)
            except httpx.HTTPError as exc:
                last = f"network error: {exc}"
                if attempt == 5:
                    break
                self.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 400 and ("json_validate_failed" in r.text or "Failed to validate JSON" in r.text):
                return None  # the model produced invalid JSON for the schema: retryable per Groq docs
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("retry-after", 2 ** attempt))
                last = f"HTTP {r.status_code}, retry-after {wait:.0f}s"
                if waited + wait > MAX_RATE_LIMIT_WAIT_S:
                    break
                waited += wait
                self.sleep(wait)
                continue
            raise GroqError(f"Groq HTTP {r.status_code}: {r.text[:300]}")
        raise GroqError(f"Groq request did not succeed ({last}; waited {waited:.0f}s of {MAX_RATE_LIMIT_WAIT_S}s "
                        "allowed). Free-tier limits: try again later.")

    def _parse(self, *, model: str, max_tokens: int, system: str, messages: list[dict], output_format,
               output_config: dict | None = None, **_: Any):
        schema = output_format.model_json_schema()
        chat = [{"role": "system", "content": system + "\n\nReply only with a JSON object that matches the "
                                                        "required schema."}]
        for m in messages:
            content = m["content"]
            chat.append({"role": m["role"],
                         "content": _convert_content(content) if isinstance(content, list) else content})
        payload: dict[str, Any] = {
            "model": model, "messages": chat, "max_completion_tokens": max_tokens, "temperature": 0.2,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": output_format.__name__, "strict": True,
                                                "schema": strict_schema(schema)}},
        }
        if model.startswith("openai/gpt-oss") and output_config and output_config.get("effort"):
            payload["reasoning_effort"] = {"low": "low", "medium": "medium"}.get(output_config["effort"], "high")
        data = self._post(payload)
        if data is None:
            return SimpleNamespace(parsed_output=None, stop_reason="invalid_json",
                                   usage=SimpleNamespace(input_tokens=0, output_tokens=0))
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        usage = data.get("usage", {})
        try:
            parsed = output_format.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError):
            parsed = None  # LLM.structured treats this as a schema failure and retries once
        return SimpleNamespace(
            parsed_output=parsed,
            stop_reason="max_tokens" if choice.get("finish_reason") == "length" else "end_turn",
            usage=SimpleNamespace(input_tokens=usage.get("prompt_tokens", 0),
                                  output_tokens=usage.get("completion_tokens", 0)))
