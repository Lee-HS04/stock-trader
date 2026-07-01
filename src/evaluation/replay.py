from __future__ import annotations

from typing import Any

from .failure_labels import label_decision_failures
from .schemas import normalize_action, validate_decision


def evaluate_replay_case(case: dict[str, Any]) -> dict[str, Any]:
    decision = case["decision"]
    expectations = case.get("expectations", {})
    issues = validate_decision(decision, sensor_price=case.get("sensor_price"), max_per_trade=case.get("max_per_trade"))
    labels = label_decision_failures(decision)

    checks = []
    if "allowed_actions" in expectations:
        action = normalize_action(decision.get("action"))
        allowed = {normalize_action(a) for a in expectations["allowed_actions"]}
        checks.append({"name": "allowed_action", "passed": action in allowed, "value": action, "expected": sorted(allowed)})
    if "required_labels" in expectations:
        for label in expectations["required_labels"]:
            checks.append({"name": f"required_label:{label}", "passed": label in labels, "value": labels, "expected": label})
    if "forbidden_labels" in expectations:
        for label in expectations["forbidden_labels"]:
            checks.append({"name": f"forbidden_label:{label}", "passed": label not in labels, "value": labels, "expected": f"not {label}"})
    if expectations.get("must_be_valid", True):
        checks.append({"name": "valid_contract", "passed": not issues, "value": [i.code for i in issues], "expected": []})

    return {
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "issues": [issue.__dict__ for issue in issues],
        "failure_labels": labels,
    }

