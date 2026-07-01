from __future__ import annotations

from typing import Any

from .schemas import normalize_action


def label_decision_failures(decision: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    action = normalize_action(decision.get("action"))

    rsi = _nested_float(decision, "technical", "rsi")
    confidence = _float_or_none(decision.get("confidence"))
    requested = _float_or_none(decision.get("requested_capital"))
    max_allowed = _float_or_none(decision.get("max_allowed_capital"))

    spy_price = _nested_float(decision, "macro", "spy_price")
    spy_sma = _nested_float(decision, "macro", "spy_sma")
    bearish_macro = bool(spy_price is not None and spy_sma is not None and spy_price < spy_sma)

    memory_warnings = _listish(decision.get("memory_warnings")) or _listish(
        _nested_value(decision, "memory", "warnings")
    )
    media_warnings = _listish(decision.get("media_warnings")) or _listish(
        _nested_value(decision, "media", "warnings")
    )
    evidence_conflicts = _listish(decision.get("evidence_conflicts"))

    if action == "BUY" and rsi is not None and rsi > 70:
        labels.append("bad_entry_overbought_buy")
    if action == "SELL" and rsi is not None and rsi < 30:
        labels.append("bad_exit_oversold_sell")
    if action == "BUY" and bearish_macro and confidence is not None and confidence >= 0.7:
        labels.append("ignored_bearish_macro")
    if action == "BUY" and memory_warnings:
        labels.append("traded_against_memory_warning")
    if action == "BUY" and media_warnings:
        labels.append("traded_against_media_warning")
    if action in {"BUY", "SELL"} and evidence_conflicts and confidence is not None and confidence >= 0.7:
        labels.append("high_confidence_despite_conflicting_evidence")
    if (
        action == "BUY"
        and requested is not None
        and max_allowed is not None
        and requested >= max_allowed * 0.95
        and (confidence is None or confidence < 0.65 or evidence_conflicts or bearish_macro)
    ):
        labels.append("oversized_low_conviction_trade")
    if action == "SKIP" and rsi is not None and rsi < 30 and not bearish_macro and not memory_warnings:
        labels.append("possible_overconservative_skip")

    return sorted(set(labels))


def label_trade_outcomes(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labeled: list[dict[str, Any]] = []
    for trade in trades:
        labels: list[str] = []
        pnl = _float_or_none(trade.get("pnl")) or 0.0
        action = normalize_action(trade.get("action") or trade.get("side"))
        if action == "SELL":
            if pnl > 0:
                labels.append("winning_closed_trade")
            elif pnl < 0:
                labels.append("losing_closed_trade")
            else:
                labels.append("flat_closed_trade")
        item = dict(trade)
        item["failure_labels"] = labels
        labeled.append(item)
    return labeled


def repeated_failure_counts(labeled_decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in labeled_decisions:
        for label in item.get("failure_labels", []):
            counts[label] = counts.get(label, 0) + 1
    return counts


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested_value(data: dict[str, Any], *keys: str) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _nested_float(data: dict[str, Any], *keys: str) -> float | None:
    return _float_or_none(_nested_value(data, *keys))


def _listish(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]

