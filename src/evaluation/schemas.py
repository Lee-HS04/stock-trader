from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


VALID_ACTIONS = {"BUY", "SELL", "SKIP"}
REQUIRED_DECISION_FIELDS = {
    "ticker",
    "action",
    "price",
    "confidence",
    "requested_capital",
    "thesis",
    "bear_case",
    "tool_evidence",
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"


def normalize_action(action: Any) -> str:
    return str(action or "").strip().upper()


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    path = Path(path)
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                records.append(
                    {
                        "_malformed": True,
                        "_line_no": line_no,
                        "_error": str(exc),
                        "_raw": line,
                    }
                )
                continue
            if isinstance(item, dict):
                records.append(item)
            else:
                records.append(
                    {
                        "_malformed": True,
                        "_line_no": line_no,
                        "_error": "JSONL record is not an object",
                        "_raw": item,
                    }
                )
    return records


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def validate_decision(
    decision: dict[str, Any],
    *,
    sensor_price: float | None = None,
    max_per_trade: float | None = None,
    price_tolerance: float = 1e-6,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if decision.get("_malformed"):
        return [
            ValidationIssue(
                "malformed_json",
                f"Malformed JSONL record: {decision.get('_error', 'unknown error')}",
            )
        ]

    missing = sorted(REQUIRED_DECISION_FIELDS - decision.keys())
    for field in missing:
        issues.append(ValidationIssue("missing_required_field", f"Missing field: {field}"))

    action = normalize_action(decision.get("action"))
    if action not in VALID_ACTIONS:
        issues.append(ValidationIssue("invalid_action", f"Invalid action: {decision.get('action')}"))

    try:
        confidence = float(decision.get("confidence"))
        if confidence < 0 or confidence > 1:
            issues.append(ValidationIssue("confidence_out_of_range", "Confidence must be between 0 and 1"))
    except (TypeError, ValueError):
        issues.append(ValidationIssue("invalid_confidence", "Confidence must be numeric"))

    try:
        requested_capital = float(decision.get("requested_capital"))
        if requested_capital < 0:
            issues.append(ValidationIssue("negative_requested_capital", "Requested capital cannot be negative"))
        if max_per_trade is not None and requested_capital > max_per_trade:
            issues.append(
                ValidationIssue(
                    "max_per_trade_exceeded",
                    f"Requested capital {requested_capital} exceeds max {max_per_trade}",
                )
            )
    except (TypeError, ValueError):
        issues.append(ValidationIssue("invalid_requested_capital", "Requested capital must be numeric"))

    price_reference = sensor_price
    if price_reference is None and decision.get("sensor_price") is not None:
        try:
            price_reference = float(decision["sensor_price"])
        except (TypeError, ValueError):
            issues.append(ValidationIssue("invalid_sensor_price", "Sensor price must be numeric"))

    if price_reference is not None:
        try:
            price = float(decision.get("price"))
            if abs(price - price_reference) > price_tolerance:
                issues.append(
                    ValidationIssue(
                        "price_hallucination",
                        f"Decision price {price} does not match sensor price {price_reference}",
                    )
                )
        except (TypeError, ValueError):
            issues.append(ValidationIssue("invalid_price", "Decision price must be numeric"))

    evidence = decision.get("tool_evidence")
    if action in {"BUY", "SELL"} and not evidence:
        issues.append(ValidationIssue("missing_trade_evidence", "BUY/SELL decisions require tool evidence"))
    if action == "SKIP" and not decision.get("thesis"):
        issues.append(ValidationIssue("missing_skip_reason", "SKIP decisions require a reason"))

    return issues


def validate_tool_call(
    call: dict[str, Any],
    *,
    require_backtest_sync: bool = False,
    expected_date: str | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if call.get("_malformed"):
        return [ValidationIssue("malformed_json", f"Malformed tool call: {call.get('_error', 'unknown error')}")]

    status = str(call.get("status", "success")).lower()
    if status in {"error", "failed", "failure"}:
        issues.append(ValidationIssue("tool_call_failed", str(call.get("error") or "Tool call failed")))

    if require_backtest_sync:
        arg_text = " ".join(str(a) for a in _extract_args(call))
        if "--backtest" not in arg_text:
            issues.append(ValidationIssue("missing_backtest_flag", "Backtest tool call missing --backtest"))
        if "--date" not in arg_text:
            issues.append(ValidationIssue("missing_backtest_date", "Backtest tool call missing --date"))
        if expected_date and expected_date not in arg_text:
            issues.append(
                ValidationIssue("wrong_backtest_date", f"Backtest tool call does not include date {expected_date}")
            )

    return issues


def _extract_args(call: dict[str, Any]) -> Iterable[Any]:
    args = call.get("args")
    if isinstance(args, list):
        return args
    if isinstance(args, dict):
        flattened: list[Any] = []
        for key, value in args.items():
            flattened.extend([key, value])
        return flattened
    command = call.get("command")
    if command:
        return str(command).split()
    return []


def issue_counts(issues: Iterable[ValidationIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.code] = counts.get(issue.code, 0) + 1
    return counts

