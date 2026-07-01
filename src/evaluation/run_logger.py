from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[2]


def active_run_dir() -> Path | None:
    explicit = os.getenv("EVAL_RUN_DIR")
    if explicit:
        return Path(explicit)

    run_id = os.getenv("EVAL_RUN_ID")
    if run_id:
        return APP_ROOT / "runs" / run_id

    return None


def is_enabled() -> bool:
    return active_run_dir() is not None


def append_jsonl(filename: str, record: dict[str, Any]) -> None:
    run_dir = active_run_dir()
    if run_dir is None:
        return

    run_dir.mkdir(parents=True, exist_ok=True)
    enriched = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    with (run_dir / filename).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(enriched, sort_keys=True) + "\n")


def log_tool_call(
    *,
    tool: str,
    args: dict[str, Any] | None = None,
    command: str | None = None,
    status: str = "success",
    output: dict[str, Any] | str | None = None,
    error: str | None = None,
) -> None:
    append_jsonl(
        "tool_calls.jsonl",
        {
            "tool": tool,
            "args": args or {},
            "command": command,
            "status": status,
            "output": output,
            "error": error,
        },
    )


def log_decision_from_proposal(
    *,
    proposal: dict[str, Any] | None,
    result: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> None:
    proposal = proposal or {}
    action = str(proposal.get("action", "")).upper()
    price = proposal.get("proposed_price")
    actual_price = proposal.get("actual_price")

    record = {
        "ticker": proposal.get("firm"),
        "action": action,
        "price": price,
        "sensor_price": actual_price,
        "confidence": proposal.get("confidence", 0.0),
        "requested_capital": proposal.get("amount", 0.0),
        "max_allowed_capital": (config or {}).get("max_per_trade"),
        "thesis": proposal.get("thesis") or proposal.get("reason") or result.get("reason", ""),
        "bear_case": proposal.get("bear_case", ""),
        "tool_evidence": proposal.get("tool_evidence", []),
        "technical": proposal.get("technical", {}),
        "macro": proposal.get("macro", {}),
        "memory": proposal.get("memory", {}),
        "media": proposal.get("media", {}),
        "memory_warnings": proposal.get("memory_warnings", []),
        "media_warnings": proposal.get("media_warnings", []),
        "evidence_conflicts": proposal.get("evidence_conflicts", []),
        "sanity_status": result.get("status"),
        "sanity_reason": result.get("reason"),
    }
    append_jsonl("decisions.jsonl", record)


def log_trade(
    *,
    firm: str,
    price: float,
    quantity: float,
    action: str,
    pnl: float,
    balance: float,
    timestamp: str,
) -> None:
    append_jsonl(
        "trades.jsonl",
        {
            "firm": firm,
            "ticker": firm,
            "price": price,
            "quantity": quantity,
            "action": action,
            "pnl": pnl,
            "balance": balance,
            "timestamp": timestamp,
        },
    )


def log_portfolio_snapshot(*, timestamp: str, portfolio: dict[str, Any]) -> None:
    run_dir = active_run_dir()
    if run_dir is None:
        return

    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "portfolio_timeseries.csv"
    write_header = not path.exists()
    balance = float(portfolio.get("balance", 0.0))

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["timestamp", "balance", "equity", "stocks_json"],
        )
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": timestamp,
                "balance": balance,
                "equity": balance,
                "stocks_json": json.dumps(portfolio.get("stocks", {}), sort_keys=True),
            }
        )


def log_run_config(config: dict[str, Any]) -> None:
    run_dir = active_run_dir()
    if run_dir is None:
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "config.json"
    if not path.exists():
        path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")

