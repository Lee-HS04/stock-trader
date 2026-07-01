from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .metrics import compute_harness_metrics, load_run_artifacts
from .scorecard import score_run
from .schemas import write_json


def evaluate_walk_forward(
    evolution_dir: str | Path,
    validation_dir: str | Path,
    holdout_dir: str | Path,
    *,
    target_failure_label: str | None = None,
    min_validation_failure_reduction: float = 0.20,
    benchmark_return: float = 0.0,
) -> dict[str, Any]:
    evolution_score = score_run(evolution_dir, benchmark_return=benchmark_return)
    validation_score = score_run(validation_dir, benchmark_return=benchmark_return)
    holdout_score = score_run(holdout_dir, benchmark_return=benchmark_return)

    evolution_failures = _failure_counts(evolution_dir)
    validation_failures = _failure_counts(validation_dir)
    holdout_failures = _failure_counts(holdout_dir)

    labels = [target_failure_label] if target_failure_label else sorted(evolution_failures.keys())
    reductions = {}
    checks = []
    for label in labels:
        before = evolution_failures.get(label, 0)
        validation = validation_failures.get(label, 0)
        holdout = holdout_failures.get(label, 0)
        reduction = (before - validation) / before if before else 0.0
        holdout_reduction = (before - holdout) / before if before else 0.0
        reductions[label] = {
            "evolution_count": before,
            "validation_count": validation,
            "holdout_count": holdout,
            "validation_reduction": reduction,
            "holdout_reduction": holdout_reduction,
        }
        if before:
            checks.append(
                {
                    "name": f"{label}_validation_reduction",
                    "passed": reduction >= min_validation_failure_reduction,
                    "value": reduction,
                    "expected": f">= {min_validation_failure_reduction}",
                }
            )
            checks.append(
                {
                    "name": f"{label}_holdout_not_worse",
                    "passed": holdout <= before,
                    "value": holdout,
                    "expected": f"<= {before}",
                }
            )

    checks.append(
        {
            "name": "validation_score_positive",
            "passed": validation_score["score"] > 0,
            "value": validation_score["score"],
            "expected": "> 0",
        }
    )
    checks.append(
        {
            "name": "holdout_score_positive",
            "passed": holdout_score["score"] > 0,
            "value": holdout_score["score"],
            "expected": "> 0",
        }
    )

    return {
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "failure_reductions": reductions,
        "evolution": evolution_score,
        "validation": validation_score,
        "holdout": holdout_score,
    }


def _failure_counts(run_dir: str | Path) -> dict[str, int]:
    artifacts = load_run_artifacts(run_dir)
    harness = compute_harness_metrics(artifacts["decisions"], artifacts["tool_calls"])
    return harness["failure_label_counts"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate walk-forward self-improvement.")
    parser.add_argument("--evolution", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--holdout", required=True)
    parser.add_argument("--target-failure-label")
    parser.add_argument("--benchmark-return", type=float, default=0.0)
    parser.add_argument("--out")
    args = parser.parse_args()

    result = evaluate_walk_forward(
        args.evolution,
        args.validation,
        args.holdout,
        target_failure_label=args.target_failure_label,
        benchmark_return=args.benchmark_return,
    )
    if args.out:
        write_json(args.out, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

