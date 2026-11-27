"""One run of the full-matrix verification: a sweep, its rescore, the expected results and the record.

A run is one agent type through one entry point. Its sweep scores all twelve metrics in
Foundry, and the customer-process rescore of that scorecard scores the same Attempts in this
process. `record_of` compares both with the reference and gives every cell its status, by
the rules declared in docs/coverage_matrix/readme.md.
"""

from __future__ import annotations

import json
import time
from fractions import Fraction

from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import RescoreFailed, rescore_in_customer_process, run_sweep
from tests.full_matrix.declaration import (
    SUPPLEMENT_METRIC_IDS,
    MatrixSetup,
    Witness,
    managed_metrics,
    native_calls_differ,
    native_tool_calls_of,
    witnessed,
)
from tests.full_matrix.reference import METRIC_IDS, expected_results

TOLERANCE = 1e-12
PATHS = ("foundry_managed", "customer_process")
PREDICTABILITY = ("calibration", "strict_confidence_discrimination", "brier_based_score")
# The metrics that the service's events are a witness for only when they are what the agent target exposed.
RESTS_ON_NATIVE_CALLS = (
    "distributional_trajectory_consistency",
    "sequential_trajectory_consistency",
    "resource_consistency",
)
# What a result is, whichever path calculated it.
RESULT = ("state", "value", "reason", "counts", "details", "evidence")


def declared_unsupported(metric_id: str, agent_kind: str, follow_up_supported: bool) -> dict | None:
    """Why a cell is declared unsupported before the run, and the reason its unavailable measurement is to give."""
    if metric_id in PREDICTABILITY and agent_kind == "hosted":
        return {
            "why": "by decision, a hosted agent is not asked for a confidence rating (ADR 0009)",
            "reason": "no valid confidence/outcome pair",
        }
    if metric_id in PREDICTABILITY and not follow_up_supported:
        return {
            "why": "the agent target declares that it does not support an isolated confidence follow-up",
            "reason": "no valid confidence/outcome pair",
        }
    if metric_id == "fault_robustness" and agent_kind == "prompt":
        return {
            "why": "a tool that answers with an HTTP error fails the whole invocation of a prompt agent",
            "reason": "no valid Attempt under the fault variation",
        }
    return None


def run(
    *, setup: MatrixSetup, target, target_module, agent_configuration, managed_scoring, artifact_dir=None, supplement=False
) -> dict:
    """Make the run's sweep and rescore, and return what they gave with what was witnessed of every Attempt.

    `target` is the agent target of `target_module`, made with `compose_input=setup.instruction`.
    With `supplement`, the sweep is the supplement's: its three Tasks and its two metrics.
    """
    witness = Witness(target)
    action_evidence = native_tool_calls_of(target_module)
    metric_ids = SUPPLEMENT_METRIC_IDS if supplement else METRIC_IDS
    started = time.monotonic()
    scorecard = run_sweep(
        experiments=setup.supplement(agent_configuration) if supplement else setup.experiments(agent_configuration),
        agent_target=witness,
        metrics=managed_metrics(metric_ids),
        action_evidence=action_evidence,
        managed_scoring=managed_scoring,
        artifact_dir=artifact_dir,
    )
    seconds = round(time.monotonic() - started)
    # As a customer has it: the scorecard read back from its JSON.
    earlier = json.loads(to_json(scorecard))
    rescored, refused = {}, {}
    for metric_id in metric_ids:
        # One metric at a time, so that a rescore that is refused costs no other metric its rescore.
        try:
            again = rescore_in_customer_process(
                earlier_scorecard=earlier, metrics=[metric_id], action_evidence=action_evidence
            )
        except RescoreFailed as failure:
            refused[metric_id] = str(failure)
            continue
        rescored[metric_id] = _entry(again, metric_id)["customer_process_rescore"]
    return {
        "scorecard": earlier,
        "sweep_seconds": seconds,
        "rescored": rescored,
        "rescore_refused": refused,
        "metric_ids": metric_ids,
        "facts": witnessed(setup, witness),
        "native_calls_differ": native_calls_differ(setup, witness),
        "setup": setup,
        "witness": witness,
    }


def _entry(scorecard: dict, metric_id: str) -> dict:
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def record_of(made: dict, *, agent_kind: str, entry_point: str, follow_up_supported: bool) -> dict:
    """The record of a run: every Attempt as witnessed, and every metric with its expected result and its two cells."""
    scorecard, facts = made["scorecard"], made["facts"]
    expected = expected_results(facts)
    recorded = {attempt["attempt_id"]: attempt for attempt in scorecard["attempts"]}
    metrics = {}
    for metric_id in made["metric_ids"]:
        managed = _entry(scorecard, metric_id)
        rescore = made["rescored"].get(metric_id)
        unsupported = declared_unsupported(metric_id, agent_kind, follow_up_supported)
        no_witness = (
            f"the native tool calls differ from the calls the service received in {sorted(made['native_calls_differ'])}, "
            "so the service is no witness for the declared source"
            if metric_id in RESTS_ON_NATIVE_CALLS and made["native_calls_differ"]
            else None
        )
        results = {
            "foundry_managed": _result_of(managed, failure=(managed.get("execution_failure") or {}).get("reason")),
            "customer_process": _result_of(rescore, failure=made["rescore_refused"].get(metric_id)),
        }
        metrics[metric_id] = {
            "expected": _number(expected[metric_id]),
            "declared_unsupported": unsupported,
            "results": results,
            "cells": {
                path: _cell(results[path], expected[metric_id], unsupported, no_witness, managed=path == "foundry_managed")
                for path in PATHS
            },
            "paths_agree_in_full": rescore is not None
            and managed.get("state") is not None
            and all(managed.get(name) == rescore.get(name) for name in RESULT),
            "managed_scoring": _where_scored(managed),
        }
    return {
        "agent_kind": agent_kind,
        "entry_point": entry_point,
        "scorecard_status": scorecard["status"],
        # What the scorecard names as missing, when it is partial: Attempts that did not complete, or a result.
        "scorecard_missing": [
            {name: missing.get(name) for name in ("kind", "experiment_id", "metric_id", "reason") if missing.get(name)}
            | {"attempts_not_completed": [entry["attempt_id"] for entry in missing.get("attempts_not_completed") or []]}
            for missing in scorecard.get("missing") or []
        ],
        "sweep_seconds": made["sweep_seconds"],
        "attempts": [_attempt_record(fact, made, recorded.get(fact.attempt_id)) for fact in facts],
        "native_calls_differ": made["native_calls_differ"],
        "metrics": metrics,
    }


def _result_of(entry: dict | None, *, failure: str | None) -> dict:
    """What a path gave for a metric: its state, value, reason and fidelity, or why it gave nothing."""
    if entry is None or entry.get("state") is None:
        return {"state": None, "value": None, "reason": None, "fidelity": None, "no_result": failure or "no result"}
    return {
        "state": entry["state"],
        "value": entry["value"],
        "reason": entry.get("reason"),
        "fidelity": entry.get("fidelity"),
        "no_result": None,
    }


def _cell(result: dict, expected, unsupported: dict | None, no_witness: str | None, *, managed: bool) -> dict:
    """The status of one cell, by the declared rules, and why."""
    if result["state"] is None:
        return {"status": "failed", "why": f"the path gave no result: {result['no_result']}"}
    if managed and result["fidelity"] != "authoritative":
        return {"status": "failed", "why": f"the Foundry-managed result is {result['fidelity']}, not authoritative"}
    if no_witness:
        return {"status": "unverified", "why": no_witness}
    if expected is None:
        if result["state"] != "unavailable" or result["value"] is not None:
            return {
                "status": "failed",
                "why": f"expected an unavailable measurement, and the path gave {result['state']} {result['value']!r}",
            }
        if unsupported is None:
            return {
                "status": "unverified",
                "why": "the live evidence gave the metric nothing to calculate from. The unavailable measurement "
                f"is the expected one: {result['reason']}",
            }
        if result["reason"] != unsupported["reason"]:
            return {
                "status": "unverified",
                "why": f"declared unsupported with the reason {unsupported['reason']!r}, and the run did not show "
                f"that: the measurement is unavailable for another reason, {result['reason']!r}",
            }
        return {"status": "unsupported", "why": f"{unsupported['why']}. As expected: {result['reason']}"}
    if result["state"] != "available" or result["value"] is None:
        return {
            "status": "failed",
            "why": f"expected the value {_number(expected)!r}, and the path gave {result['state']}: {result['reason']}",
        }
    if abs(result["value"] - float(expected)) > TOLERANCE:
        return {"status": "failed", "why": f"expected the value {_number(expected)!r}, and the path gave {result['value']!r}"}
    corrected = " The declaration that this cell is unsupported was wrong for this run." if unsupported else ""
    return {"status": "worked", "why": f"the value equals the expected value.{corrected}"}


def _number(value):
    """An expected value as the record holds it: the double, and the exact fraction where there is one."""
    if value is None:
        return None
    if isinstance(value, Fraction):
        return {"value": float(value), "exact": f"{value.numerator}/{value.denominator}"}
    return {"value": float(value), "exact": None}


def _where_scored(managed: dict) -> dict:
    """Where in the project the Foundry-managed result came from. No link to the portal: it names the subscription."""
    scoring = managed.get("managed_scoring") or {}
    return {
        "evaluator": scoring.get("evaluator"),
        "eval_id": scoring.get("eval_id"),
        "run_id": scoring.get("run_id"),
        "encoded_result": scoring.get("encoded_result"),
        "fidelity_check": {
            name: (scoring.get("fidelity_check") or {}).get(name) for name in ("outcome", "plain_value", "encoded_value")
        },
        "error": scoring.get("error"),
    }


def _attempt_record(fact, made: dict, recorded: dict | None) -> dict:
    """One Attempt as the reference was given it, beside what the scorecard recorded of it."""
    setup, witness = made["setup"], made["witness"]
    evidence = setup.at_verify.get(fact.attempt_id) or {"events": []}
    at_clean_up = setup.at_clean_up.get(fact.attempt_id)
    invocation = witness.invocations[fact.attempt_id]
    asked = witness.answers.get(fact.attempt_id)
    recorded = recorded or {}
    platform = recorded.get("invocation") or {}
    return {
        "attempt_id": fact.attempt_id,
        "witnessed": {
            "invocation_returned": invocation["returned"],
            "invocation_raised": invocation.get("raised"),
            "condition_observed": fact.condition_observed,
            "service_events": [
                [event["tool"], event["todo_id"], event["status"]]
                + [flag for flag in ("fault_injected", "wrapped", "note_presented") if event[flag]]
                for event in evidence["events"]
            ],
            "success": fact.success,
            "violation_weight": None if fact.violation_weight is None else float(fact.violation_weight),
            "native_tool_names": invocation.get("tool_names"),
            "elapsed_reported": invocation.get("elapsed"),
            "confidence_answer": None if asked is None else asked.get("answer"),
            "confidence_request_raised": None if asked is None else asked.get("raised"),
            # The follow-up is isolated only if the service recorded nothing between the two readings.
            "service_unchanged_after_verification": None if at_clean_up is None else at_clean_up == evidence,
        },
        "scorecard": {
            "execution_status": recorded.get("execution_status"),
            "execution_error": recorded.get("execution_error"),
            "task_outcome": (recorded.get("task_outcome") or {}).get("success"),
            "confidence": {
                name: (recorded.get("confidence") or {}).get(name) for name in ("rating", "unavailable_reason")
            },
            "entry_point": platform.get("entry_point"),
            "response_id": platform.get("response_id"),
            "eval_id": platform.get("eval_id"),
            "run_id": platform.get("run_id"),
            "total_tokens": (platform.get("token_usage") or {}).get("total_tokens"),
        },
    }
