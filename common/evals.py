"""Shared evaluation helpers: one CSV format for every task, plus summary statistics.

Failed cases are always kept. Nothing here invents numbers; it only aggregates what was measured.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent.parent / "evals_results"


@dataclass
class EvalRow:
    task: str
    case_id: str
    model: str
    expected: str
    actual: str
    quality: float  # 0..1 score for this case
    latency_ms: int
    cost: float
    passed: bool
    failure_reason: str = ""
    group: str = ""  # case type, e.g. "screenshot_only" or "classification"


def write_rows(name: str, rows: list[EvalRow]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"{name}_{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[fl.name for fl in fields(EvalRow)])
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))
    return path


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


def summarize(rows: list[EvalRow]) -> dict:
    by_model: dict[str, list[EvalRow]] = {}
    for r in rows:
        by_model.setdefault(r.model, []).append(r)
    out = {}
    for model, rs in by_model.items():
        lat = [r.latency_ms for r in rs]
        groups: dict[str, list[EvalRow]] = {}
        for r in rs:
            groups.setdefault(r.group or "all", []).append(r)
        out[model] = {
            "cases": len(rs),
            "pass_rate": round(sum(r.passed for r in rs) / len(rs), 3),
            "mean_quality": round(statistics.mean(r.quality for r in rs), 3),
            "p50_latency_ms": percentile(lat, 50),
            "p95_latency_ms": percentile(lat, 95),
            "total_cost_usd": round(sum(r.cost for r in rs), 5),
            "cost_per_case_usd": round(sum(r.cost for r in rs) / len(rs), 6),
            "by_group": {g: {"cases": len(x), "pass_rate": round(sum(r.passed for r in x) / len(x), 3)}
                         for g, x in sorted(groups.items())},
        }
    return out


def save_summary(name: str, summary: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}_summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return path


def print_summary(title: str, summary: dict) -> None:
    print(f"\n=== {title} ===")
    for model, s in summary.items():
        print(f"{model}: {s['cases']} cases, pass {s['pass_rate']:.0%}, quality {s['mean_quality']:.2f}, "
              f"p50 {s['p50_latency_ms']} ms, p95 {s['p95_latency_ms']} ms, ${s['cost_per_case_usd']:.5f}/case")
        for g, gs in s["by_group"].items():
            print(f"    {g}: {gs['pass_rate']:.0%} of {gs['cases']}")


INFRA_ERROR_MARKERS = ("getaddrinfo failed", "network error", "rate limit", "did not succeed", "Failed to validate JSON",
                       "quota exhausted")


class QuotaGuard:
    """Stops an eval loop after `limit` consecutive quota failures, so a run never fills up with fake failures."""

    def __init__(self, limit: int = 3) -> None:
        self.limit, self.streak = limit, 0

    def check(self, failure_reason: str) -> bool:
        """Record one case; return True if the run should stop."""
        self.streak = self.streak + 1 if "quota exhausted" in failure_reason.lower() else 0
        return self.streak >= self.limit


def read_rows(path: Path) -> list[EvalRow]:
    with Path(path).open(encoding="utf-8") as f:
        return [EvalRow(**{**r, "quality": float(r["quality"]), "latency_ms": int(r["latency_ms"]),
                           "cost": float(r["cost"]), "passed": r["passed"] == "True"}) for r in csv.DictReader(f)]


def combine_reruns(base: list[EvalRow], reruns: list[EvalRow]) -> tuple[list[EvalRow], int]:
    """Replace rows that failed for infrastructure reasons (network, rate limit, invalid JSON) with their re-run.
    Genuine model failures are never replaced. Returns (rows, number replaced)."""
    rerun = {(r.model, r.case_id): r for r in reruns}
    out, replaced = [], 0
    for r in base:
        infra = not r.passed and any(m in r.failure_reason for m in INFRA_ERROR_MARKERS)
        if infra and (r.model, r.case_id) in rerun:
            out.append(rerun[(r.model, r.case_id)])
            replaced += 1
        else:
            out.append(r)
    return out, replaced
