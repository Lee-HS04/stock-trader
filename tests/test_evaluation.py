import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from src.evaluation.compare_runs import compare_model_groups, compare_runs
from src.evaluation.failure_labels import label_decision_failures
from src.evaluation.replay import evaluate_replay_case
from src.evaluation.schemas import validate_decision, validate_tool_call
from src.evaluation.scorecard import score_run
from src.evaluation.walk_forward import evaluate_walk_forward


def write_jsonl(path, records):
    with Path(path).open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def make_decision(ticker="AAPL", action="BUY", **overrides):
    decision = {
        "ticker": ticker,
        "action": action,
        "price": 100.0,
        "sensor_price": 100.0,
        "confidence": 0.7,
        "requested_capital": 5000.0,
        "max_allowed_capital": 10000.0,
        "thesis": "Technical and macro evidence support the decision.",
        "bear_case": "Signal could fail if macro weakens.",
        "tool_evidence": ["trading_data", "memory_gate"],
        "technical": {"rsi": 28.0},
        "macro": {"spy_price": 500.0, "spy_sma": 490.0},
    }
    decision.update(overrides)
    return decision


def make_tool_call(status="success", date="2025-03-03"):
    return {
        "tool": "trading_data",
        "command": f"python src/trading_data.py --firm AAPL --backtest --date {date}",
        "status": status,
    }


def make_run(root, name, decisions=None, tool_calls=None, trades=None, portfolio_rows=None):
    run_dir = Path(root) / name
    run_dir.mkdir()
    write_jsonl(run_dir / "decisions.jsonl", decisions or [])
    write_jsonl(run_dir / "tool_calls.jsonl", tool_calls or [])
    write_jsonl(run_dir / "trades.jsonl", trades or [])
    if portfolio_rows:
        with (run_dir / "portfolio_timeseries.csv").open("w", encoding="utf-8") as handle:
            handle.write("timestamp,equity\n")
            for timestamp, equity in portfolio_rows:
                handle.write(f"{timestamp},{equity}\n")
    return run_dir


class EvaluationTests(unittest.TestCase):
    def test_decision_contract_validation_accepts_valid_decision(self):
        issues = validate_decision(make_decision(), max_per_trade=10000.0)
        self.assertEqual([], issues)

    def test_decision_contract_validation_rejects_hallucinated_price(self):
        issues = validate_decision(make_decision(price=105.0), max_per_trade=10000.0)
        self.assertIn("price_hallucination", {issue.code for issue in issues})

    def test_tool_call_backtest_sync_validation(self):
        good = validate_tool_call(make_tool_call(), require_backtest_sync=True, expected_date="2025-03-03")
        bad = validate_tool_call(
            {"tool": "trading_data", "command": "python src/trading_data.py --firm AAPL", "status": "success"},
            require_backtest_sync=True,
            expected_date="2025-03-03",
        )
        self.assertEqual([], good)
        self.assertIn("missing_backtest_flag", {issue.code for issue in bad})
        self.assertIn("missing_backtest_date", {issue.code for issue in bad})

    def test_failure_label_detects_ignored_bearish_macro(self):
        labels = label_decision_failures(
            make_decision(macro={"spy_price": 480.0, "spy_sma": 500.0}, confidence=0.9)
        )
        self.assertIn("ignored_bearish_macro", labels)

    def test_replay_case_evaluates_expectations(self):
        result = evaluate_replay_case(
            {
                "sensor_price": 100.0,
                "max_per_trade": 10000.0,
                "decision": make_decision(action="SKIP", thesis="Macro conditions are hostile."),
                "expectations": {"allowed_actions": ["SKIP"], "forbidden_labels": ["ignored_bearish_macro"]},
            }
        )
        self.assertTrue(result["passed"], result)

    def test_scorecard_scores_batch_and_detects_anti_gaming(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run(
                tmp,
                "good",
                decisions=[make_decision(action="BUY"), make_decision(action="SELL")],
                tool_calls=[make_tool_call(), make_tool_call()],
                trades=[
                    {"firm": "AAPL", "action": "buy", "price": 100, "quantity": 10, "pnl": 0},
                    {"firm": "AAPL", "action": "sell", "price": 110, "quantity": 10, "pnl": 100},
                ],
                portfolio_rows=[("2025-03-03", 100000), ("2025-03-04", 100100)],
            )
            result = score_run(run_dir, require_backtest_sync=True, expected_date="2025-03-03")
            self.assertTrue(result["passed"], result["checks"])
            self.assertGreater(result["score"], 0)

            skip_dir = make_run(
                tmp,
                "skip",
                decisions=[make_decision(action="SKIP")],
                tool_calls=[make_tool_call()],
                trades=[],
            )
            skip_result = score_run(skip_dir)
            self.assertTrue(skip_result["anti_gaming"]["skip_everything"])
            self.assertFalse(skip_result["passed"])

    def test_compare_runs_prefers_candidate_with_better_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = make_run(
                tmp,
                "baseline",
                decisions=[make_decision()],
                tool_calls=[make_tool_call()],
                trades=[{"firm": "AAPL", "action": "sell", "price": 95, "quantity": 10, "pnl": -50}],
            )
            candidate = make_run(
                tmp,
                "candidate",
                decisions=[make_decision()],
                tool_calls=[make_tool_call()],
                trades=[{"firm": "AAPL", "action": "sell", "price": 110, "quantity": 10, "pnl": 100}],
            )
            result = compare_runs(baseline, candidate)
            self.assertTrue(result["passed"], result["checks"])
            self.assertGreater(result["deltas"]["score"], 0)

    def test_cross_model_group_detects_lower_variance_and_better_worst_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            b1 = make_run(
                tmp,
                "b1",
                decisions=[make_decision()],
                tool_calls=[make_tool_call()],
                trades=[{"firm": "AAPL", "action": "sell", "price": 90, "quantity": 10, "pnl": -100}],
            )
            b2 = make_run(
                tmp,
                "b2",
                decisions=[make_decision()],
                tool_calls=[make_tool_call(status="error")],
                trades=[{"firm": "AAPL", "action": "sell", "price": 130, "quantity": 10, "pnl": 300}],
            )
            c1 = make_run(
                tmp,
                "c1",
                decisions=[make_decision()],
                tool_calls=[make_tool_call()],
                trades=[{"firm": "AAPL", "action": "sell", "price": 110, "quantity": 10, "pnl": 100}],
            )
            c2 = make_run(
                tmp,
                "c2",
                decisions=[make_decision()],
                tool_calls=[make_tool_call()],
                trades=[{"firm": "AAPL", "action": "sell", "price": 111, "quantity": 10, "pnl": 110}],
            )
            result = compare_model_groups([b1, b2], [c1, c2])
            self.assertTrue(result["passed"], result["checks"])

    def test_walk_forward_self_improvement_checks_failure_reduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            bearish_bad = make_decision(
                macro={"spy_price": 480.0, "spy_sma": 500.0},
                confidence=0.9,
            )
            normal = make_decision(confidence=0.6)
            evolution = make_run(
                tmp,
                "evolution",
                decisions=[bearish_bad, bearish_bad],
                tool_calls=[make_tool_call(), make_tool_call()],
                trades=[{"firm": "AAPL", "action": "sell", "price": 110, "quantity": 10, "pnl": 100}],
            )
            validation = make_run(
                tmp,
                "validation",
                decisions=[bearish_bad, normal],
                tool_calls=[make_tool_call(), make_tool_call()],
                trades=[{"firm": "AAPL", "action": "sell", "price": 110, "quantity": 10, "pnl": 100}],
            )
            holdout = make_run(
                tmp,
                "holdout",
                decisions=[normal, normal],
                tool_calls=[make_tool_call(), make_tool_call()],
                trades=[{"firm": "AAPL", "action": "sell", "price": 110, "quantity": 10, "pnl": 100}],
            )
            result = evaluate_walk_forward(
                evolution,
                validation,
                holdout,
                target_failure_label="ignored_bearish_macro",
                min_validation_failure_reduction=0.2,
            )
            self.assertTrue(result["passed"], result["checks"])


class ToolBehaviorTests(unittest.TestCase):
    def test_sanity_checker_approves_and_rejects_expected_proposals(self):
        import src.sanity_checker as sanity_checker

        with tempfile.TemporaryDirectory() as tmp:
            account_path = Path(tmp) / "account.json"
            config_path = Path(tmp) / "config.json"
            account_path.write_text(json.dumps({"balance": 100000, "stocks": {}}), encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    {
                        "initial_balance": 100000,
                        "max_per_trade": 10000,
                        "total_budget": 50000,
                        "blacklist": [],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(sanity_checker, "account", account_path), patch.object(
                sanity_checker, "CONFIG_PATH", config_path
            ):
                approved = sanity_checker.check_trade(
                    json.dumps(
                        {
                            "firm": "AAPL",
                            "amount": 1000,
                            "proposed_price": 100,
                            "actual_price": 100,
                            "action": "buy",
                        }
                    )
                )
                rejected = sanity_checker.check_trade(
                    json.dumps(
                        {
                            "firm": "AAPL",
                            "amount": 1000,
                            "proposed_price": 120,
                            "actual_price": 100,
                            "action": "buy",
                        }
                    )
                )
            self.assertEqual("APPROVED", approved["status"])
            self.assertEqual("REJECTED", rejected["status"])

    def test_trade_executor_updates_temp_account_and_db(self):
        import src.trade_executor as trade_executor

        with tempfile.TemporaryDirectory() as tmp:
            account_path = Path(tmp) / "account.json"
            db_path = Path(tmp) / "trade_history.db"
            with patch.object(trade_executor, "account_path", account_path), patch.object(
                trade_executor, "database_path", db_path
            ), redirect_stdout(StringIO()):
                trade_executor.trade("AAPL", 100.0, 2, "buy")
                trade_executor.trade("AAPL", 110.0, 2, "sell")

            account = json.loads(account_path.read_text(encoding="utf-8"))
            self.assertEqual(100020.0, account["balance"])

            conn = sqlite3.connect(db_path)
            rows = conn.execute("SELECT firm, action, pnl FROM trade_history ORDER BY trade_id").fetchall()
            conn.close()
            self.assertEqual([("AAPL", "buy", 0.0), ("AAPL", "sell", 20.0)], rows)

    def test_auditor_reads_executor_schema(self):
        from src.auditor import IntegratedQuantitativeAuditor

        with tempfile.TemporaryDirectory() as tmp:
            account_path = Path(tmp) / "account.json"
            peers_path = Path(tmp) / "competitors.json"
            db_path = Path(tmp) / "trade_history.db"
            account_path.write_text(json.dumps({"balance": 100020, "stocks": {}}), encoding="utf-8")
            peers_path.write_text(
                json.dumps(
                    {
                        "Market_Baseline": {"return": 0.0},
                        "Strategic_Master": {"return": 0.0},
                    }
                ),
                encoding="utf-8",
            )
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE trade_history(trade_id INTEGER PRIMARY KEY, firm TEXT, price FLOAT, quantity INTEGER, action TEXT, pnl FLOAT, timestamp TEXT)"
            )
            conn.execute(
                "INSERT INTO trade_history(firm, price, quantity, action, pnl, timestamp) VALUES ('AAPL', 110, 2, 'sell', 20, '2025-03-04')"
            )
            conn.commit()
            conn.close()

            report = json.loads(
                IntegratedQuantitativeAuditor(
                    db_path=str(db_path),
                    account_path=str(account_path),
                    peers_path=str(peers_path),
                ).generate_audit_report()
            )
            self.assertEqual(1, report["performance"]["trade_count"])
            self.assertEqual(20.0, report["performance"]["realized_pnl"])


class ArtifactCaptureTests(unittest.TestCase):
    def test_sanity_checker_cli_writes_decision_and_tool_call_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            env = os_environ_with({"EVAL_RUN_DIR": str(run_dir)})
            proposal = json.dumps(
                {
                    "firm": "AAPL",
                    "amount": 1000,
                    "proposed_price": 100,
                    "actual_price": 100,
                    "action": "buy",
                    "confidence": 0.7,
                    "thesis": "Fixture thesis",
                    "tool_evidence": ["fixture"],
                }
            )
            completed = subprocess.run(
                [sys.executable, "src/sanity_checker.py", "--proposal", proposal],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("APPROVED", completed.stdout)
            self.assertTrue((run_dir / "decisions.jsonl").exists())
            self.assertTrue((run_dir / "tool_calls.jsonl").exists())

            decision = json.loads((run_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual("AAPL", decision["ticker"])
            self.assertEqual("APPROVED", decision["sanity_status"])

    def test_trade_executor_logs_trade_and_portfolio_snapshot_when_run_dir_is_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            env = os_environ_with({"EVAL_RUN_DIR": str(run_dir)})
            cwd = Path(__file__).resolve().parents[1]

            account_path = cwd / "account.json"
            db_path = cwd / "trade_history.db"
            original_account = account_path.read_text(encoding="utf-8") if account_path.exists() else None
            original_db = db_path.read_bytes() if db_path.exists() else None
            try:
                account_path.write_text(
                    json.dumps({"balance": 100000.0, "stocks": {}, "cost_basis": {}}),
                    encoding="utf-8",
                )
                if db_path.exists():
                    db_path.unlink()

                subprocess.run(
                    [
                        sys.executable,
                        "src/trade_executor.py",
                        "--firm",
                        "AAPL",
                        "--price",
                        "100",
                        "--quantity",
                        "2",
                        "--action",
                        "buy",
                    ],
                    cwd=cwd,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertTrue((run_dir / "trades.jsonl").exists())
                self.assertTrue((run_dir / "portfolio_timeseries.csv").exists())
                self.assertTrue((run_dir / "tool_calls.jsonl").exists())

                trade = json.loads((run_dir / "trades.jsonl").read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual("AAPL", trade["firm"])
                self.assertEqual("buy", trade["action"])
            finally:
                if original_account is None:
                    if account_path.exists():
                        account_path.unlink()
                else:
                    account_path.write_text(original_account, encoding="utf-8")

                if db_path.exists():
                    db_path.unlink()
                if original_db is not None:
                    db_path.write_bytes(original_db)


def os_environ_with(overrides):
    import os

    env = os.environ.copy()
    env.update(overrides)
    return env


if __name__ == "__main__":
    unittest.main()
