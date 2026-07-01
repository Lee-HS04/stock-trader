from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from .failure_labels import label_decision_failures
from .schemas import issue_counts, load_jsonl, normalize_action, validate_decision, validate_tool_call


def load_run_artifacts(run_dir: str | Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    return {
        "decisions": load_jsonl(run_dir / "decisions.jsonl"),
        "tool_calls": load_jsonl(run_dir / "tool_calls.jsonl"),
        "trades": load_jsonl(run_dir / "trades.jsonl"),
        "portfolio": load_portfolio_timeseries(run_dir / "portfolio_timeseries.csv"),
    }


def load_portfolio_timeseries(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def compute_trading_metrics(
    trades: list[dict[str, Any]],
    *,
    portfolio_timeseries: list[dict[str, Any]] | None = None,
    initial_balance: float = 100000.0,
    benchmark_return: float = 0.0,
) -> dict[str, Any]:
    normalized = [_normalize_trade(t) for t in trades if not t.get("_malformed")]
    sells = [t for t in normalized if t["action"] == "SELL"]
    realized_pnl = sum(t["pnl"] for t in normalized)
    wins = [t["pnl"] for t in sells if t["pnl"] > 0]
    losses = [t["pnl"] for t in sells if t["pnl"] < 0]

    total_return = realized_pnl / initial_balance if initial_balance else 0.0
    profit_factor = sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else 0.0)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    equity_curve = _equity_curve(normalized, portfolio_timeseries or [], initial_balance)
    max_drawdown = _max_drawdown(equity_curve)

    return {
        "trade_count": len(normalized),
        "closed_trades": len(sells),
        "realized_pnl": round(realized_pnl, 6),
        "total_return": total_return,
        "benchmark_return": benchmark_return,
        "alpha_vs_benchmark": total_return - benchmark_return,
        "win_rate": len(wins) / len(sells) if sells else 0.0,
        "profit_factor": profit_factor,
        "average_win": avg_win,
        "average_loss": avg_loss,
        "average_win_loss_ratio": avg_win / abs(avg_loss) if avg_loss else math.inf if avg_win else 0.0,
        "max_drawdown": max_drawdown,
    }


def compute_harness_metrics(
    decisions: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    *,
    max_per_trade: float | None = None,
    require_backtest_sync: bool = False,
    expected_date: str | None = None,
) -> dict[str, Any]:
    decision_issues = []
    labeled_decisions = []
    for decision in decisions:
        decision_issues.extend(validate_decision(decision, max_per_trade=max_per_trade))
        item = dict(decision)
        item["failure_labels"] = label_decision_failures(decision)
        labeled_decisions.append(item)

    tool_issues = []
    for call in tool_calls:
        tool_issues.extend(
            validate_tool_call(call, require_backtest_sync=require_backtest_sync, expected_date=expected_date)
        )

    action_counts: dict[str, int] = {}
    for decision in decisions:
        action = normalize_action(decision.get("action"))
        if action:
            action_counts[action] = action_counts.get(action, 0) + 1

    failure_label_counts: dict[str, int] = {}
    for item in labeled_decisions:
        for label in item["failure_labels"]:
            failure_label_counts[label] = failure_label_counts.get(label, 0) + 1

    total_decisions = len(decisions)
    total_tool_calls = len(tool_calls)
    malformed = sum(1 for d in decisions if d.get("_malformed"))

    decision_issue_counts = issue_counts(decision_issues)
    tool_issue_counts = issue_counts(tool_issues)

    return {
        "decision_count": total_decisions,
        "tool_call_count": total_tool_calls,
        "action_counts": action_counts,
        "skip_rate": action_counts.get("SKIP", 0) / total_decisions if total_decisions else 0.0,
        "malformed_decision_count": malformed + decision_issue_counts.get("malformed_json", 0),
        "malformed_decision_rate": (malformed + decision_issue_counts.get("malformed_json", 0)) / total_decisions
        if total_decisions
        else 0.0,
        "price_hallucination_count": decision_issue_counts.get("price_hallucination", 0),
        "sanity_rejection_count": sum(
            1 for d in decisions if str(d.get("sanity_status", "")).upper() == "REJECTED"
        ),
        "tool_failure_count": tool_issue_counts.get("tool_call_failed", 0),
        "tool_failure_rate": tool_issue_counts.get("tool_call_failed", 0) / total_tool_calls
        if total_tool_calls
        else 0.0,
        "temporal_sync_error_count": tool_issue_counts.get("missing_backtest_flag", 0)
        + tool_issue_counts.get("missing_backtest_date", 0)
        + tool_issue_counts.get("wrong_backtest_date", 0),
        "decision_issue_counts": decision_issue_counts,
        "tool_issue_counts": tool_issue_counts,
        "failure_label_counts": failure_label_counts,
        "labeled_decisions": labeled_decisions,
    }


def compute_anti_gaming_metrics(
    decisions: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    min_average_position_size: float = 1.0,
) -> dict[str, Any]:
    normalized = [_normalize_trade(t) for t in trades if not t.get("_malformed")]
    buys = [t for t in normalized if t["action"] == "BUY"]
    sells = [t for t in normalized if t["action"] == "SELL"]
    avg_position_size = (
        sum(abs(t["price"] * t["quantity"]) for t in normalized) / len(normalized) if normalized else 0.0
    )
    decision_count = len(decisions)
    skip_count = sum(1 for d in decisions if normalize_action(d.get("action")) == "SKIP")

    return {
        "average_position_size": avg_position_size,
        "tiny_trade_behavior": bool(normalized and avg_position_size < min_average_position_size),
        "no_closed_trades": bool(buys and not sells),
        "skip_everything": bool(decision_count and skip_count == decision_count),
    }


def _normalize_trade(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": trade.get("ticker") or trade.get("firm"),
        "action": normalize_action(trade.get("action") or trade.get("side")),
        "price": _float(trade.get("price")),
        "quantity": _float(trade.get("quantity")),
        "pnl": _float(trade.get("pnl")),
        "timestamp": trade.get("timestamp"),
    }


def _equity_curve(
    trades: list[dict[str, Any]], portfolio_timeseries: list[dict[str, Any]], initial_balance: float
) -> list[float]:
    if portfolio_timeseries:
        curve = []
        for row in portfolio_timeseries:
            value = row.get("equity") or row.get("portfolio_value") or row.get("balance")
            curve.append(_float(value, default=initial_balance))
        return curve

    curve = [initial_balance]
    cumulative = 0.0
    for trade in trades:
        cumulative += trade["pnl"]
        curve.append(initial_balance + cumulative)
    return curve


def _max_drawdown(curve: list[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    max_dd = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak:
            max_dd = max(max_dd, (peak - value) / peak)
    return max_dd


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

