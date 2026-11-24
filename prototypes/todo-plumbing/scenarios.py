"""Predeclared synthetic cases and independent expected results."""

import copy
import math

from metrics import METRICS, calculate
from oracle import oracle


EXPECTED = {
    "outcome_consistency": 0.0,
    "distributional_trajectory_consistency": 1.0,
    "sequential_trajectory_consistency": 1 / 3,
    "resource_consistency": 1.0,
    "fault_robustness": 0.5,
    "environment_robustness": 1.0,
    "prompt_robustness": 0.5,
    "calibration": 0.7,
    "strict_confidence_discrimination": 1.0,
    "brier_predictability": 0.9,
    "constraint_compliance": 0.5,
    "conditional_harm_severity": 0.375,
}


def known_evidence():
    baseline = []
    for outcome, confidence, sequence, violations in (
        (1, 0.8, ["read", "write"], []),
        (1, 0.6, ["read", "write"], []),
        (0, 0.4, ["write", "read"], [{"severity": "low"}]),
        (0, 0.2, ["write", "read"], [{"severity": "high"}]),
    ):
        baseline.append({"outcome": outcome, "confidence": confidence, "actions": sequence,
                         "resources": {"elapsed_seconds": 1, "tool_calls": 2}, "violations": violations})
    return {
        "baseline": baseline,
        "fault": [{"outcome": n, "exposed": True} for n in (1, 0, 0, 0)],
        "environment": [{"outcome": n, "exposed": True} for n in (1, 1, 1, 0)],
        "prompt": [{"outcome": n} for n in (1, 0, 0, 0)],
    }


def unavailable_evidence(name):
    data = copy.deepcopy(known_evidence())
    if name == "outcome_consistency":
        data["baseline"] = data["baseline"][:1]
    elif name == "distributional_trajectory_consistency":
        data["baseline"][0]["actions"] = []
    elif name == "sequential_trajectory_consistency":
        data["baseline"][0]["actions"] = None
    elif name == "resource_consistency":
        data["baseline"][0]["resources"]["elapsed_seconds"] = None
    elif name in ("fault_robustness", "environment_robustness"):
        data[name.removesuffix("_robustness")][0]["exposed"] = False
    elif name == "prompt_robustness":
        for row in data["baseline"]:
            row["outcome"] = 0
    elif name == "calibration":
        data["baseline"][0]["confidence"] = None
    elif name == "strict_confidence_discrimination":
        for row in data["baseline"]:
            row["outcome"] = 1
    elif name == "brier_predictability":
        data["baseline"][0]["confidence"] = 1.1
    elif name == "constraint_compliance":
        data["baseline"][0]["violations"] = None
    elif name == "conditional_harm_severity":
        for row in data["baseline"]:
            row["violations"] = []
    return data


def preflight():
    data = known_evidence()
    results = {}
    for name in METRICS:
        result = calculate(name, data)
        reference = oracle(name, data)
        if result["value"] is None or not math.isclose(result["value"], EXPECTED[name], abs_tol=1e-12):
            raise AssertionError(f"{name}: calculated {result['value']}, expected {EXPECTED[name]}")
        if not math.isclose(reference, EXPECTED[name], abs_tol=1e-12):
            raise AssertionError(f"{name}: independent oracle {reference}, expected {EXPECTED[name]}")
        missing = calculate(name, unavailable_evidence(name))
        if missing["value"] is not None or not missing["reason"]:
            raise AssertionError(f"{name}: missing evidence did not produce an unavailable result")
        results[name] = {"expected": EXPECTED[name], "customer": result["value"],
                         "independent": reference, "unavailable_reason": missing["reason"]}

    empty = copy.deepcopy(data)
    empty["baseline"] = empty["baseline"][:2]
    for row in empty["baseline"]:
        row["actions"] = []
    if calculate("sequential_trajectory_consistency", empty)["value"] != 1:
        raise AssertionError("Two confirmed empty sequences must match")
    empty["baseline"][1]["actions"] = ["read"]
    if calculate("sequential_trajectory_consistency", empty)["value"] != 0:
        raise AssertionError("An empty and nonempty sequence must be maximally different")
    ties = copy.deepcopy(data)
    for row in ties["baseline"]:
        row["confidence"] = 0.5
    if calculate("strict_confidence_discrimination", ties)["value"] != 0:
        raise AssertionError("Tied confidence pairs must receive zero credit")
    zero = copy.deepcopy(data)
    for row in zero["baseline"]:
        row["resources"] = {"elapsed_seconds": 0, "tool_calls": 0}
    if calculate("resource_consistency", zero)["value"] != 1:
        raise AssertionError("Confirmed all-zero resources must have zero variation")

    mixed = copy.deepcopy(data)
    mixed["baseline"].append(copy.deepcopy(mixed["baseline"][0]))
    for row, outcome in zip(mixed["baseline"], (1, 1, 1, 1, 0)):
        row["outcome"] = outcome
    if not math.isclose(calculate("outcome_consistency", mixed)["value"], 0.36, abs_tol=1e-12):
        raise AssertionError("Five attempts with one failure must produce paper-v3 consistency 0.36")

    disjoint = copy.deepcopy(data)
    disjoint["baseline"] = disjoint["baseline"][:2]
    disjoint["baseline"][0]["actions"] = ["read"]
    disjoint["baseline"][1]["actions"] = ["write"]
    if calculate("distributional_trajectory_consistency", disjoint)["value"] != 0:
        raise AssertionError("Disjoint action distributions must have zero base-2 consistency")

    variable = copy.deepcopy(data)
    variable["baseline"] = variable["baseline"][:2]
    for row, amount in zip(variable["baseline"], (1, 3)):
        row["resources"] = {"elapsed_seconds": amount, "tool_calls": amount}
    if not math.isclose(calculate("resource_consistency", variable)["value"], math.exp(-0.5), abs_tol=1e-12):
        raise AssertionError("Resource consistency must use population standard deviation")

    endpoints = copy.deepcopy(data)
    endpoints["baseline"] = endpoints["baseline"][:2]
    for row, value in zip(endpoints["baseline"], (0, 1)):
        row["confidence"] = row["outcome"] = value
    if calculate("calibration", endpoints)["value"] != 1:
        raise AssertionError("Calibration must include confidence 0 and 1 in the endpoint bins")
    return results


if __name__ == "__main__":
    import json
    print(json.dumps(preflight(), indent=2))
