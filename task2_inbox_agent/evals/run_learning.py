"""Measurable self-improvement experiment (see EVALS.md).

1. Draft 20 dev emails with the active drafter prompt; a reviewer edits them (simulated house-style reviewer by
   default, clearly labelled) and the edits are stored like real UI edits.
2. Propose a new drafter version from those edits (Claude Sonnet).
3. Score current vs candidate on the 15 HELD-OUT emails (never used for learning); activate only if better.

  python -m task2_inbox_agent.evals.run_learning [--train 20]
Requires the Knowledge Brain API (python run.py) and GROQ_API_KEY (or ANTHROPIC_API_KEY for Claude).
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from common.kb_client import KnowledgeClient
from common.llm import LLM
from task2_inbox_agent.agent import PROMPTS, InboxAgent
from task2_inbox_agent.dataset import load
from task2_inbox_agent.learning import compare_and_maybe_activate, propose_version
from task2_inbox_agent.simulated_reviewer import review
from task2_inbox_agent.store import InboxStore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=20)
    args = ap.parse_args()
    db = Path(tempfile.mkdtemp()) / "learning.db"
    agent = InboxAgent(InboxStore(db), KnowledgeClient(requester="inbox-learning"), LLM(), prompts=PROMPTS)
    agent.autonomy.set_enabled(False, "learning-experiment")

    train = [e for e in load("dev") if e.expected.category in ("how_to", "troubleshooting", "follow_up")][:args.train]
    edited = 0
    for item in train:
        r = agent.process(item.email)
        if not r.draft:
            continue
        row = agent.store.interaction(r.interaction_id)
        urls = [s["url"] for s in json.loads(row["kb_response"])["sources"]] if row["kb_response"] else []
        verdict = review(r.draft, item.email.first_name, urls)
        if verdict.unchanged:
            agent.send_reviewed(r.interaction_id, r.draft, "simulated-reviewer")
        else:
            edited += 1
            agent.send_reviewed(r.interaction_id, verdict.final_text, "simulated-reviewer",
                                "house style: " + ", ".join(verdict.violations))
    print(f"Training drafts: {len(train)}, edited by reviewer: {edited}")

    version, learned = propose_version(agent.store, agent.llm, PROMPTS)
    print(f"Proposed drafter {version}:")
    for g in learned.guidelines:
        print("  -", g)
    record = compare_and_maybe_activate(agent, load("holdout"), version, review)
    for side in ("current", "candidate"):
        s = record[side]
        print(f"{side:9s} {s['version']}: edit rate {s['edit_rate']:.0%}, violations {s['violation_rate']:.0%}, "
              f"similarity {s['mean_similarity']:.2f}, cost ${s['cost_usd']:.4f}, latency {s['mean_latency_ms']:.0f} ms")
    print("Activated." if record["activated"] else "Not activated (no improvement or safety got worse).")


if __name__ == "__main__":
    main()
