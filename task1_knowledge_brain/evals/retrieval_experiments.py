"""Compare evidence-selection strategies on the fixed answerable eval questions (no LLM calls unless --expand).

  python -m task1_knowledge_brain.evals.retrieval_experiments [--strategies baseline,siblings,rerank12,...]

Metrics per strategy: hit@5 / hit@8 (an expected article among the first 5 / 8 passages), MRR, rank of the
K32 Stripe article, K09 evidence completeness (all passages holding the four mandatory import fields present),
mean evidence size, mean latency per query, and model requests per query.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import yaml

from common.evals import RESULTS_DIR
from common.llm import HAIKU
from task1_knowledge_brain.evals.run_evals import copy_service
from task1_knowledge_brain.retrieval import strategies

CASES = [c for c in yaml.safe_load((Path(__file__).parent / "kb_cases.yaml").read_text(encoding="utf-8"))
         if c["expect"] == "answered"]
K09_FIELDS = ["listing_type", "country_code", "profession_name", "Name or Company"]


def evaluate(name: str, select, svc) -> dict:
    hit5 = hit8 = 0
    rr: list[float] = []
    sizes, latencies = [], []
    k32_rank = None
    k09_complete = None
    per_case = {}
    for c in CASES:
        start = time.perf_counter()
        hits, requests = select(c["question"])
        latencies.append((time.perf_counter() - start) * 1000)
        sizes.append(len(hits))
        ranks = [i for i, h in enumerate(hits, start=1) if h.article_id in c["expected_articles"]]
        first = ranks[0] if ranks else None
        hit5 += bool(first and first <= 5)
        hit8 += bool(first and first <= 8)
        rr.append(1 / first if first else 0.0)
        per_case[c["id"]] = first
        if c["id"] == "K32":
            k32_rank = first
        if c["id"] == "K09":
            evidence = " ".join(h.text for h in hits)
            k09_complete = all(f in evidence for f in K09_FIELDS)
    n = len(CASES)
    return {"strategy": name, "cases": n, "hit@5": round(hit5 / n, 3), "hit@8": round(hit8 / n, 3),
            "mrr": round(statistics.mean(rr), 3), "k32_rank": k32_rank, "k09_evidence_complete": k09_complete,
            "mean_passages": round(statistics.mean(sizes), 1), "mean_latency_ms": round(statistics.mean(latencies), 1),
            "model_requests_per_query": requests, "first_expected_rank": per_case}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="baseline,siblings,rerank12,rerank12_siblings,bge_rerank,bge_rerank_siblings")
    args = ap.parse_args()
    svc = copy_service()
    idx = svc.index
    rerankers: dict[str, strategies.Reranker] = {}

    def rr(model: str) -> strategies.Reranker:
        if model not in rerankers:
            rerankers[model] = strategies.Reranker(model)
        return rerankers[model]

    options = {
        "baseline": lambda q: (strategies.baseline(idx, q), 0),
        "siblings": lambda q: (strategies.add_siblings(idx, q, strategies.baseline(idx, q)), 0),
        "rerank12": lambda q: (strategies.reranked(idx, rr("Xenova/ms-marco-MiniLM-L-12-v2"), q), 0),
        "rerank12_siblings": lambda q: (strategies.add_siblings(
            idx, q, strategies.reranked(idx, rr("Xenova/ms-marco-MiniLM-L-12-v2"), q)), 0),
        "bge_rerank": lambda q: (strategies.reranked(idx, rr("BAAI/bge-reranker-base"), q), 0),
        "bge_rerank_siblings": lambda q: (strategies.add_siblings(
            idx, q, strategies.reranked(idx, rr("BAAI/bge-reranker-base"), q)), 0),
        "expand": lambda q: strategies.expanded(idx, svc.llm, q, HAIKU),
        "expand_siblings": lambda q: (lambda hr: (strategies.add_siblings(idx, q, hr[0]), hr[1]))(
            strategies.expanded(idx, svc.llm, q, HAIKU)),
    }
    results = []
    for name in args.strategies.split(","):
        if not name.startswith("expand"):
            options[name]("warm up")  # load models outside the timed loop (no model call wasted for expand)
        r = evaluate(name, options[name], svc)
        results.append(r)
        print(f"{name:20s} hit@5 {r['hit@5']:.0%}  hit@8 {r['hit@8']:.0%}  MRR {r['mrr']:.3f}  K32 rank {r['k32_rank']}  "
              f"K09 complete {r['k09_evidence_complete']}  passages {r['mean_passages']}  {r['mean_latency_ms']:.0f} ms/query")
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / "retrieval_experiments.json"
    previous = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    merged = {r["strategy"]: r for r in previous} | {r["strategy"]: r for r in results}
    out.write_text(json.dumps(list(merged.values()), indent=2), encoding="utf-8")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
