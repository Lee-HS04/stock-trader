from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .metrics import compute_anti_gaming_metrics, compute_harness_metrics, compute_trading_metrics, load_run_artifacts
from .schemas import write_json


DEFAULT_THRESHOLDS = {
    "max_tool_failure_rate": 0.02,
    "max_malformed_decision_rate": 0.01,
    "max_price_hallucinations": 0,
    "max_temporal_sync_errors": 0,
    "min_closed_trades": 1,
    "max_drawdown": 0.25,
    "max_skip_rate": 0.95,
    "min_profit_factor": 0.0,
}


def score_run(
    run_dir: str | Path,
    *,
    benchmark_return: float = 0.0,
    initial_balance: float = 100000.0,
    max_per_trade: float | None = None,
    require_backtest_sync: bool = False,
    expected_date: str | None = None,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    artifacts = load_run_artifacts(run_dir)
    trading = compute_trading_metrics(
        artifacts["trades"],
        portfolio_timeseries=artifacts["portfolio"],
        initial_balance=initial_balance,
        benchmark_return=benchmark_return,
    )
    harness = compute_harness_metrics(
        artifacts["decisions"],
        artifacts["tool_calls"],
        max_per_trade=max_per_trade,
        require_backtest_sync=require_backtest_sync,
        expected_date=expected_date,
    )
    anti_gaming = compute_anti_gaming_metrics(artifacts["decisions"], artifacts["trades"])
    score = compute_harness_score(trading, harness, anti_gaming)
    checks = evaluate_thresholds(trading, harness, anti_gaming, thresholds or DEFAULT_THRESHOLDS)

    return {
        "run_dir": str(Path(run_dir)),
        "score": score,
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "trading": trading,
        "harness": {k: v for k, v in harness.items() if k != "labeled_decisions"},
        "anti_gaming": anti_gaming,
    }


def compute_harness_score(
    trading: dict[str, Any], harness: dict[str, Any], anti_gaming: dict[str, Any]
) -> float:
    profit_factor = trading.get("profit_factor", 0.0)
    if profit_factor == math.inf:
        profit_factor_component = 1.0
    else:
        profit_factor_component = min(float(profit_factor) / 2.0, 1.0)

    score = 0.0
    score += 25.0 * _clip01((float(trading.get("alpha_vs_benchmark", 0.0)) + 0.1) / 0.2)
    score += 20.0 * _clip01(float(trading.get("win_rate", 0.0)))
    score += 15.0 * profit_factor_component
    score -= 20.0 * _clip01(float(trading.get("max_drawdown", 0.0)) / 0.25)
    score -= 10.0 * _clip01(float(harness.get("tool_failure_rate", 0.0)) / 0.1)
    score -= 5.0 * _clip01(float(harness.get("malformed_decision_rate", 0.0)) / 0.1)
    score -= 5.0 * _clip01(float(harness.get("skip_rate", 0.0)))

    if anti_gaming.get("skip_everything"):
        score -= 25.0
    if anti_gaming.get("tiny_trade_behavior"):
        score -= 10.0
    if anti_gaming.get("no_closed_trades"):
        score -= 10.0

    return round(score, 4)


def evaluate_thresholds(
    trading: dict[str, Any],
    harness: dict[str, Any],
    anti_gaming: dict[str, Any],
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    checks = [
        _check(
            "tool_failure_rate",
            harness.get("tool_failure_rate", 0.0) <= thresholds["max_tool_failure_rate"],
            harness.get("tool_failure_rate", 0.0),
            f"<= {thresholds['max_tool_failure_rate']}",
        ),
        _check(
            "malformed_decision_rate",
            harness.get("malformed_decision_rate", 0.0) <= thresholds["max_malformed_decision_rate"],
            harness.get("malformed_decision_rate", 0.0),
            f"<= {thresholds['max_malformed_decision_rate']}",
        ),
        _check(
            "price_hallucination_count",
            harness.get("price_hallucination_count", 0) <= thresholds["max_price_hallucinations"],
            harness.get("price_hallucination_count", 0),
            f"<= {thresholds['max_price_hallucinations']}",
        ),
        _check(
            "temporal_sync_error_count",
            harness.get("temporal_sync_error_count", 0) <= thresholds["max_temporal_sync_errors"],
            harness.get("temporal_sync_error_count", 0),
            f"<= {thresholds['max_temporal_sync_errors']}",
        ),
        _check(
            "closed_trades",
            trading.get("closed_trades", 0) >= thresholds["min_closed_trades"],
            trading.get("closed_trades", 0),
            f">= {thresholds['min_closed_trades']}",
        ),
        _check(
            "max_drawdown",
            trading.get("max_drawdown", 0.0) <= thresholds["max_drawdown"],
            trading.get("max_drawdown", 0.0),
            f"<= {thresholds['max_drawdown']}",
        ),
        _check(
            "skip_rate",
            harness.get("skip_rate", 0.0) <= thresholds["max_skip_rate"],
            harness.get("skip_rate", 0.0),
            f"<= {thresholds['max_skip_rate']}",
        ),
        _check(
            "profit_factor",
            trading.get("profit_factor", 0.0) >= thresholds["min_profit_factor"],
            trading.get("profit_factor", 0.0),
            f">= {thresholds['min_profit_factor']}",
        ),
        _check("anti_skip_everything", not anti_gaming.get("skip_everything"), anti_gaming.get("skip_everything"), "False"),
        _check("anti_tiny_trades", not anti_gaming.get("tiny_trade_behavior"), anti_gaming.get("tiny_trade_behavior"), "False"),
    ]
    return checks


def _check(name: str, passed: bool, value: Any, expected: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "value": value, "expected": expected}


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a stock-trader evaluation run.")
    parser.add_argument("--run", required=True, help="Run artifact directory.")
    parser.add_argument("--benchmark-return", type=float, default=0.0)
    parser.add_argument("--initial-balance", type=float, default=100000.0)
    parser.add_argument("--max-per-trade", type=float)
    parser.add_argument("--require-backtest-sync", action="store_true")
    parser.add_argument("--expected-date")
    parser.add_argument("--out", help="Optional path for metrics JSON.")
    args = parser.parse_args()

    result = score_run(
        args.run,
        benchmark_return=args.benchmark_return,
        initial_balance=args.initial_balance,
        max_per_trade=args.max_per_trade,
        require_backtest_sync=args.require_backtest_sync,
        expected_date=args.expected_date,
    )
    if args.out:
        write_json(args.out, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

