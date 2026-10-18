"""Test helpers shared by all tasks: a fake Anthropic client (no network, no cost)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable


class FakeAnthropic:
    """Stands in for anthropic.Anthropic. `responder(kwargs)` returns the parsed object (or an Exception)."""

    def __init__(self, responder: Callable[[dict], object]) -> None:
        self.calls: list[dict] = []
        self.responder = responder
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        parsed = self.responder(kwargs)
        if isinstance(parsed, Exception):
            raise parsed
        return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))
