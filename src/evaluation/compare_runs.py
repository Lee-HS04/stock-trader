from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .scorecard import score_run
from .schemas import write_json


DEFAULT_COMPARE_THRESHOLDS = {
    "min_score_delta": 0.0,
    "min_alpha_delta": 0.0,
    "min_win_rate_delta": 0.0,
    "max_drawdown_worsening": 0.10,
    "max_tool_failure_worsening": 0.0,
    "max_variance_worsening": 0.0,
}


def compare_runs(
    baseline_dir: str | Path,
    candidate_dir: str | Path,
    *,
    benchmark_return: float = 0.0,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or DEFAULT_COMPARE_THRESHOLDS
    baseline = score_run(baseline_dir, benchmark_return=benchmark_return)
    candidate = score_run(candidate_dir, benchmark_return=benchmark_return)

    deltas = {
        "score": candidate["score"] - baseline["score"],
        "alpha_vs_benchmark": candidate["trading"]["alpha_vs_benchmark"]
        - baseline["trading"]["alpha_vs_benchmark"],
        "win_rate": candidate["trading"]["win_rate"] - baseline["trading"]["win_rate"],
        "max_drawdown": candidate["trading"]["max_drawdown"] - baseline["trading"]["max_drawdown"],
        "tool_failure_rate": candidate["harness"]["tool_failure_rate"]
        - baseline["harness"]["tool_failure_rate"],
        "malformed_decision_rate": candidate["harness"]["malformed_decision_rate"]
        - baseline["harness"]["malformed_decision_rate"],
    }

    checks = [
        _check("score_delta", deltas["score"] >= thresholds["min_score_delta"], deltas["score"], f">= {thresholds['min_score_delta']}"),
        _check(
            "alpha_delta",
            deltas["alpha_vs_benchmark"] >= thresholds["min_alpha_delta"],
            deltas["alpha_vs_benchmark"],
            f">= {thresholds['min_alpha_delta']}",
        ),
        _check(
            "win_rate_delta",
            deltas["win_rate"] >= thresholds["min_win_rate_delta"],
            deltas["win_rate"],
            f">= {thresholds['min_win_rate_delta']}",
        ),
        _check(
            "drawdown_worsening",
            deltas["max_drawdown"] <= thresholds["max_drawdown_worsening"],
            deltas["max_drawdown"],
            f"<= {thresholds['max_drawdown_worsening']}",
        ),
        _check(
            "tool_failure_worsening",
            deltas["tool_failure_rate"] <= thresholds["max_tool_failure_worsening"],
            deltas["tool_failure_rate"],
            f"<= {thresholds['max_tool_failure_worsening']}",
        ),
    ]

    return {
        "passed": all(c["passed"] for c in checks) and candidate["passed"],
        "checks": checks,
        "deltas": deltas,
        "baseline": baseline,
        "candidate": candidate,
    }


def compare_model_groups(
    baseline_dirs: list[str | Path],
    candidate_dirs: list[str | Path],
    *,
    benchmark_return: float = 0.0,
) -> dict[str, Any]:
    baseline_scores = [score_run(path, benchmark_return=benchmark_return) for path in baseline_dirs]
    candidate_scores = [score_run(path, benchmark_return=benchmark_return) for path in candidate_dirs]

    baseline_summary = _group_summary(baseline_scores)
    candidate_summary = _group_summary(candidate_scores)
    deltas = {
        "mean_score": candidate_summary["mean_score"] - baseline_summary["mean_score"],
        "worst_score": candidate_summary["worst_score"] - baseline_summary["worst_score"],
        "score_std_dev": candidate_summary["score_std_dev"] - baseline_summary["score_std_dev"],
        "mean_tool_failure_rate": candidate_summary["mean_tool_failure_rate"]
        - baseline_summary["mean_tool_failure_rate"],
    }

    checks = [
        _check("mean_score_improved", deltas["mean_score"] > 0, deltas["mean_score"], "> 0"),
        _check("worst_score_improved", deltas["worst_score"] > 0, deltas["worst_score"], "> 0"),
        _check("score_variance_not_worse", deltas["score_std_dev"] <= 0, deltas["score_std_dev"], "<= 0"),
    ]

    return {
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "deltas": deltas,
        "baseline_group": baseline_summary,
        "candidate_group": candidate_summary,
    }


def _group_summary(scores: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(s["score"]) for s in scores]
    tool_failure_rates = [float(s["harness"]["tool_failure_rate"]) for s in scores]
    return {
        "count": len(scores),
        "mean_score": _mean(values),
        "worst_score": min(values) if values else 0.0,
        "best_score": max(values) if values else 0.0,
        "score_std_dev": _std(values),
        "mean_tool_failure_rate": _mean(tool_failure_rates),
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = _mean(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _check(name: str, passed: bool, value: Any, expected: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "value": value, "expected": expected}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare stock-trader evaluation runs.")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--benchmark-return", type=float, default=0.0)
    parser.add_argument("--out")
    args = parser.parse_args()

    result = compare_runs(args.baseline, args.candidate, benchmark_return=args.benchmark_return)
    if args.out:
        write_json(args.out, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

