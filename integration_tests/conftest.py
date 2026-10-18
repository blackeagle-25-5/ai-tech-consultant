import pytest

import common.llm


@pytest.fixture(autouse=True)
def _ledger_to_tmp(request, tmp_path, monkeypatch):
    """Fake-model UI tests must never write to the real call ledger. Live tests keep the real ledger."""
    if "live" not in request.keywords:
        monkeypatch.setattr(common.llm, "LEDGER_PATH", tmp_path / "ledger.jsonl")
