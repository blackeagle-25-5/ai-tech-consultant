"""Thin wrapper around the Anthropic SDK.

Every model call in the project goes through `LLM` so that we get, in one place:
- structured (Pydantic-validated) output,
- retries (SDK retries network/429/5xx; we retry once more on schema failures),
- a cost / latency ledger written to `data/llm_ledger.jsonl` for the evals.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

try:  # Load .env when present; harmless when python-dotenv is missing.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"

# USD per 1M tokens (input, output). Source: Anthropic pricing table, cached 2026-10-06.
PRICES: dict[str, tuple[float, float]] = {
    HAIKU: (0.10, 0.50),
    SONNET: (2.00, 10.00),
    OPUS: (4.00, 20.00),
}

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = Path(os.getenv("LLM_LEDGER_PATH", ROOT / "data" / "llm_ledger.jsonl"))

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the model call fails after retries or returns unusable output."""


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, PRICES[SONNET])
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


@dataclass
class CallRecord:
    purpose: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    ok: bool
    attempts: int
    prompt_version: str = ""
    error: str = ""
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class LLMResult(Generic[T]):
    value: T
    record: CallRecord


def _write_ledger(record: CallRecord) -> None:
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record)) + "\n")
    except OSError:
        pass  # The ledger is best-effort; never fail a user request because of it.


class LLM:
    """Structured-output client. Pass a fake `client` in tests."""

    def __init__(self, client: Any | None = None, timeout_s: float = 90.0) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(max_retries=3, timeout=timeout_s)
        self.client = client

    def structured(
        self,
        *,
        schema: type[T],
        system: str,
        user: str | list[dict[str, Any]],
        model: str = HAIKU,
        purpose: str = "unspecified",
        prompt_version: str = "",
        max_tokens: int = 4000,
        effort: str | None = "low",
        schema_retries: int = 1,
    ) -> LLMResult[T]:
        """Call Claude and return a validated `schema` instance."""
        content = user if isinstance(user, list) else [{"type": "text", "text": user}]
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "output_format": schema,
        }
        if effort:
            kwargs["output_config"] = {"effort": effort}

        start = time.perf_counter()
        in_tok = out_tok = 0
        last_error = ""
        for attempt in range(1, schema_retries + 2):
            try:
                response = self.client.messages.parse(**kwargs)
            except Exception as exc:  # SDK already retried transient errors.
                last_error = f"{type(exc).__name__}: {exc}"
                break
            in_tok += response.usage.input_tokens
            out_tok += response.usage.output_tokens
            parsed = getattr(response, "parsed_output", None)
            if parsed is not None and response.stop_reason != "refusal":
                record = CallRecord(
                    purpose=purpose,
                    model=model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=cost_usd(model, in_tok, out_tok),
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    ok=True,
                    attempts=attempt,
                    prompt_version=prompt_version,
                )
                _write_ledger(record)
                return LLMResult(value=parsed, record=record)
            last_error = f"no parsed output (stop_reason={response.stop_reason})"

        record = CallRecord(
            purpose=purpose,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost_usd(model, in_tok, out_tok),
            latency_ms=int((time.perf_counter() - start) * 1000),
            ok=False,
            attempts=schema_retries + 1,
            prompt_version=prompt_version,
            error=last_error,
        )
        _write_ledger(record)
        raise LLMError(last_error)


def validate_json(schema: type[T], data: Any) -> T:
    """Validate a dict against a schema, raising LLMError with a readable message."""
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise LLMError(str(exc)) from exc
