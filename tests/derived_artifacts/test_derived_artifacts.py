"""Evaluator seam: the Foundry evaluators derived from the canonical metric definitions.

The vectors of every Evaluator run against its derived artifact where they are written, in
`tests/evaluators/` (see its conftest). Here is what only a derived artifact has: the sandbox
it has to fit, the evidence it is sent and the encoding its result comes back in.

A derived evaluator returns one number for each item it is given. The item names the part of
the result it asks for: the plain value, or a part of the encoded result, which carries the
state, the reason and the counts. `tests/managed_artifact.py` puts the parts together by hand.
"""

import ast
import json

import pytest

from reliability_evaluators.evaluators import RELIABILITY_METRICS, outcome_consistency
from reliability_evaluators.evidence import (
    ActionEvent,
    ActionEvidence,
    ActionTypeMapping,
    Attempt,
    Condition,
    ConfidenceRating,
    ExecutionStatus,
    LifecyclePoint,
    LifecycleStep,
    ObservedCondition,
    OperationalConstraint,
    PromptVariant,
    ResourceMeasurement,
    SeverityRating,
    TaskEvidence,
    TaskOutcome,
    ViolationAssessment,
    group_by_task,
)
from reliability_evaluators.managed_scoring import derive_artifact, has_managed_evaluator
from tests import managed_artifact

SOURCE = "synthetic customer test"
METRIC_IDS = [metric.metric_id for metric in RELIABILITY_METRICS]

# What the Foundry code-evaluator sandbox accepted and refused when observed. See docs/managed_scoring/readme.md.
SANDBOX_MODULES = {
    "__future__", "base64", "bisect", "collections", "dataclasses", "enum", "fractions", "hashlib", "itertools",
    "json", "math", "re", "typing", "zlib",
}  # fmt: skip
# A call by one of these names is refused before the code runs, as a function and as a method: re.compile too.
REFUSED_CALLS = {"compile", "eval", "exec", "globals", "__import__"}
SANDBOX_PYTHON = (3, 12)
CODE_SIZE_LIMIT = 256 * 1024


def test_each_of_the_twelve_reliability_metrics_has_a_derived_evaluator():
    assert len(METRIC_IDS) == 12
    assert [metric_id for metric_id in METRIC_IDS if not has_managed_evaluator(metric_id)] == []
    assert {derive_artifact(metric_id).evaluator_name for metric_id in METRIC_IDS} == {
        "reliability-outcome-consistency",
        "reliability-distributional-trajectory-consistency",
        "reliability-sequential-trajectory-consistency",
        "reliability-resource-consistency",
        "reliability-fault-robustness",
        "reliability-environment-robustness",
        "reliability-prompt-robustness",
        "reliability-calibration",
        "reliability-strict-confidence-discrimination",
        "reliability-brier-based-score",
        "reliability-constraint-compliance",
        "reliability-conditional-harm-severity",
    }


@pytest.mark.parametrize("metric_id", METRIC_IDS)
def test_a_derived_artifact_fits_the_code_evaluator_sandbox(metric_id):
    code_text = derive_artifact(metric_id).code_text

    imported, called = set(), set()
    for node in ast.walk(ast.parse(code_text, feature_version=SANDBOX_PYTHON)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import cannot be resolved in the sandbox"
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            called.add(getattr(node.func, "id", None) or getattr(node.func, "attr", None))
    assert imported <= SANDBOX_MODULES
    assert not called & REFUSED_CALLS
    assert len(code_text.encode("utf-8")) < CODE_SIZE_LIMIT


REWORDED = Condition(condition_id="reworded", description="The instruction in another wording.", kind="prompt")
NOT_TO_DELETE = OperationalConstraint(constraint_id="no-delete", description="Never delete an item.")

# One Attempt that records every kind of evidence an Attempt can record.
RECORDS_EVERYTHING = Attempt(
    attempt_id="close/attempt-1",
    task_id="close",
    execution_status=ExecutionStatus.COMPLETED,
    task_outcome=TaskOutcome(success=True, source=SOURCE),
    action_evidence=ActionEvidence(
        source="native tool calls",
        events=[
            ActionEvent(tool_name="get_item", arguments={"item_id": 7}, result={"title": "Renew passport"}),
            ActionEvent(tool_name="update_item", arguments={"item_id": 7, "done": True}, error="503 unavailable"),
        ],
    ),
    resource_measurements=[
        ResourceMeasurement(resource_type="tool_calls", value=2, unit="calls", boundary="counted by the service")
    ],
    invocation={"response_id": "resp_1"},
    confidence=ConfidenceRating(rating="87.50", original_response=" 87.50 %"),
    operational_constraints=[NOT_TO_DELETE],
    violation_assessments=[
        ViolationAssessment(
            constraint_id="no-delete",
            violated=True,
            evidence="item 3 is gone",
            source="state of the service",
            severity=SeverityRating(label="high"),
        )
    ],
    experiment_id="rewording",
    starting_state="fresh",
    intended_condition=REWORDED,
    condition_role="variation",
    prompt_variant=PromptVariant(variant_id="casual", text="Close it, please.", source="the support team"),
    observed_condition=ObservedCondition(met=True, source="request log", observations={"prompt": "Close it, please."}),
    lifecycle=[LifecycleStep(point=LifecyclePoint.PREPARE_STATE)],
)
RECORDED = RECORDS_EVERYTHING.to_dict()
# What every transported Attempt holds: its identity, its Task and its execution status, and nothing else.
IDENTITY = {
    **Attempt(attempt_id="close/attempt-1", task_id="close", execution_status=ExecutionStatus.COMPLETED).to_dict()
}
TOOL_NAMES_IN_ORDER = {
    "source": "native tool calls",
    "events": [
        {"tool_name": "get_item", "arguments": None, "result": None, "error": None},
        {"tool_name": "update_item", "arguments": None, "result": None, "error": None},
    ],
}
COMPARED_CONDITIONS = ("task_outcome", "intended_condition", "observed_condition", "prompt_variant")
# A metric of nominal behaviour leaves out the Attempts under a variation, so every Attempt is sent with
# the condition it was intended for.
CONFIDENCE_OUTCOME_PAIR = ("task_outcome", "confidence", "intended_condition")
ASSESSED_CONSTRAINTS = ("operational_constraints", "violation_assessments", "intended_condition")
# The evidence of an Attempt that each Evaluator is sent, by the names the Attempt record writes it under.
SUBMITTED = {
    "outcome_consistency": {name: RECORDED[name] for name in ("task_outcome", "intended_condition")},
    "distributional_trajectory_consistency": {
        "action_evidence": TOOL_NAMES_IN_ORDER,
        "intended_condition": RECORDED["intended_condition"],
    },
    "sequential_trajectory_consistency": {
        "action_evidence": TOOL_NAMES_IN_ORDER,
        "intended_condition": RECORDED["intended_condition"],
    },
    "resource_consistency": {name: RECORDED[name] for name in ("resource_measurements", "intended_condition")},
    "fault_robustness": {name: RECORDED[name] for name in COMPARED_CONDITIONS},
    "environment_robustness": {name: RECORDED[name] for name in COMPARED_CONDITIONS},
    "prompt_robustness": {name: RECORDED[name] for name in COMPARED_CONDITIONS},
    "calibration": {name: RECORDED[name] for name in CONFIDENCE_OUTCOME_PAIR},
    "strict_confidence_discrimination": {name: RECORDED[name] for name in CONFIDENCE_OUTCOME_PAIR},
    "brier_based_score": {name: RECORDED[name] for name in CONFIDENCE_OUTCOME_PAIR},
    "constraint_compliance": {name: RECORDED[name] for name in ASSESSED_CONSTRAINTS},
    "conditional_harm_severity": {name: RECORDED[name] for name in ASSESSED_CONSTRAINTS},
}
ACTION_TYPES = ActionTypeMapping(
    evidence_source="native tool calls", action_types={"get_item": "read", "update_item": "update"}
)
# What the customer declares for an Evaluator, which its `evaluate` takes after the evidence.
DECLARED = {
    "distributional_trajectory_consistency": (ACTION_TYPES,),
    "sequential_trajectory_consistency": (ACTION_TYPES,),
}


def submitted(metric_id, tasks):
    """What is submitted for a metric, read by hand: the JSON of its transported evidence."""
    artifact = derive_artifact(metric_id)
    (field, evidence), = artifact.item(tasks, *DECLARED.get(metric_id, ())).items()
    assert field == f"{metric_id}_evidence"
    return json.loads(managed_artifact.unpacked(evidence))


@pytest.mark.parametrize("metric_id", METRIC_IDS)
def test_an_attempt_is_sent_with_the_evidence_its_evaluator_is_calculated_from_and_nothing_else(metric_id):
    evidence = submitted(metric_id, group_by_task([RECORDS_EVERYTHING]))

    assert evidence["tasks"] == [{"task_id": "close", "attempts": [{**IDENTITY, **SUBMITTED[metric_id]}]}]


def test_a_confidence_rating_and_its_task_outcome_are_sent_as_they_were_recorded():
    # More digits than a double holds, and a trailing zero that a number would lose.
    ratings = ["87.50", "33.333333333333333333333333333333", "0.10000000000000000000000001", "100"]
    attempts = [
        Attempt(
            attempt_id=f"close/attempt-{number}",
            task_id="close",
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=number % 2 == 1, source=SOURCE),
            confidence=ConfidenceRating(rating=rating, original_response=f"{rating}%"),
        )
        for number, rating in enumerate(ratings, start=1)
    ]

    (task,) = submitted("brier_based_score", group_by_task(attempts))["tasks"]

    assert [(attempt["confidence"]["rating"], attempt["task_outcome"]["success"]) for attempt in task["attempts"]] == [
        ("87.50", True),
        ("33.333333333333333333333333333333", False),
        ("0.10000000000000000000000001", True),
        ("100", False),
    ]


def test_a_resource_measurement_is_sent_as_the_number_that_was_recorded():
    # A sum that is not 0.3, the smallest double, the largest, and a count that no double holds.
    recorded = [("elapsed_time", 0.1 + 0.2), ("elapsed_time", 5e-324), ("elapsed_time", 1.7976931348623157e308)]
    attempts = [
        Attempt(
            attempt_id=f"close/attempt-{number}",
            task_id="close",
            execution_status=ExecutionStatus.COMPLETED,
            resource_measurements=[
                ResourceMeasurement(resource_type=resource_type, value=value, unit="seconds", boundary="wall clock"),
                ResourceMeasurement(resource_type="token_usage", value=2**53 + number, unit="tokens", boundary="all"),
            ],
        )
        for number, (resource_type, value) in enumerate(recorded, start=1)
    ]

    (task,) = submitted("resource_consistency", group_by_task(attempts))["tasks"]
    values = [[measurement["value"] for measurement in attempt["resource_measurements"]] for attempt in task["attempts"]]

    assert values == [
        [0.30000000000000004, 9007199254740993],
        [5e-324, 9007199254740994],
        [1.7976931348623157e308, 9007199254740995],
    ]
    assert [type(value) for value in values[0]] == [float, int]


def test_the_declared_action_types_are_sent_with_the_evidence_they_apply_to():
    evidence = submitted("sequential_trajectory_consistency", group_by_task([RECORDS_EVERYTHING]))

    assert evidence["declared"] == {
        "action_types": {
            "evidence_source": "native tool calls",
            "action_types": {"get_item": "read", "update_item": "update"},
        }
    }


@pytest.mark.parametrize("metric_id", [metric_id for metric_id in METRIC_IDS if metric_id not in DECLARED])
def test_an_evaluator_that_takes_no_declaration_is_sent_none(metric_id):
    assert submitted(metric_id, group_by_task([RECORDS_EVERYTHING]))["declared"] == {}


def trajectory_attempts(*sequences):
    return [
        Attempt(
            attempt_id=f"close/attempt-{number}",
            task_id="close",
            execution_status=ExecutionStatus.COMPLETED,
            action_evidence=ActionEvidence(
                source="native tool calls", events=[ActionEvent(tool_name=name) for name in tool_names]
            ),
        )
        for number, tool_names in enumerate(sequences, start=1)
    ]


def test_action_types_that_were_changed_after_the_evidence_was_encoded_are_rejected():
    # As declared, the two Attempts take the same action types in another order: distance 2 / 2, value 0.
    # With `update_item` turned into a read, both would be two reads and the value would be 1.
    artifact = derive_artifact("sequential_trajectory_consistency")
    tasks = group_by_task(trajectory_attempts(["get_item", "update_item"], ["update_item", "get_item"]))
    item = artifact.item(tasks, ACTION_TYPES)
    assert managed_artifact.grade_item(artifact, item, "value") == 0

    changed = managed_artifact.altered_evidence(item, '"update_item":"update"', '"update_item":"read"')

    with pytest.raises(Exception, match="altered") as rejection:
        managed_artifact.grade_item(artifact, changed, "value")
    assert type(rejection.value).__name__ == "EvidenceError"


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        ({}, "action_types"),
        ({"action_types": {"evidence_source": "native tool calls"}}, "action_types"),
        ({"action_types": {"evidence_source": "", "action_types": {"get_item": "read"}}}, "evidence source"),
        ({"action_types": None, "other": None}, "other"),
    ],
)
def test_a_trajectory_artifact_rejects_intact_evidence_whose_declaration_is_not_one(declared, problem):
    artifact = derive_artifact("sequential_trajectory_consistency")
    recorded = [attempt.to_dict() for attempt in trajectory_attempts(["get_item"], ["get_item"])]
    evidence = {"encoding": 3, "declared": declared, "tasks": [{"task_id": "close", "attempts": recorded}]}
    item = {"sequential_trajectory_consistency_evidence": managed_artifact.packed(json.dumps(evidence))}

    with pytest.raises(Exception, match=problem):
        managed_artifact.grade_item(artifact, item, "value")


def attempts(task_id, outcomes):
    return [
        Attempt(
            attempt_id=f"{task_id}/attempt-{number}",
            task_id=task_id,
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=outcome, source=SOURCE),
        )
        for number, outcome in enumerate(outcomes, start=1)
    ]


def by_task(outcomes_by_task):
    return group_by_task(
        attempt for task_id, outcomes in outcomes_by_task.items() for attempt in attempts(task_id, outcomes)
    )


def derived_value(tasks):
    return managed_artifact.grade(derive_artifact("outcome_consistency"), tasks, "value")


def derived_result(tasks):
    return managed_artifact.returned_result(derive_artifact("outcome_consistency"), tasks)


def test_the_encoded_result_carries_the_counts_and_details_of_a_value():
    # "repeated" is scored: p = 4/5, (8 - 5)^2 / 25 = 0.36. "once" has one Attempt and is excluded.
    result = derived_result(by_task({"repeated": [1, 1, 1, 1, 0], "once": [1]}))

    assert result == {
        "metric_id": "outcome_consistency",
        "state": "available",
        "value": 0.36,
        "reason": None,
        "counts": {
            "tasks": {
                "total": 2,
                "valid": 1,
                "missing": [],
                "excluded": [{"reason": "fewer than two Attempts with a task outcome", "count": 1, "ids": ["once"]}],
            },
            "attempts": {
                "total": 6,
                "valid": 5,
                "missing": [],
                "excluded": [
                    {
                        "reason": "its Task has fewer than two Attempts with a task outcome",
                        "count": 1,
                        "ids": ["once/attempt-1"],
                    }
                ],
            },
        },
        "details": {"per_task": [{"task_id": "repeated", "attempts": 5, "successes": 4, "value": 0.36}]},
        "evidence": {"attempt_ids": [f"repeated/attempt-{number}" for number in range(1, 6)]},
    }


def test_the_encoded_result_carries_the_value_as_the_evaluator_computed_it():
    # steady 1, mostly (0.5)^2 = 0.25, coin 0: (1 + 0.25 + 0) / 3 = 0.41666..., which no short decimal writes.
    tasks = by_task({"steady": [1, 1, 1, 1], "mostly": [1, 1, 1, 0], "coin": [1, 0]})

    result = derived_result(tasks)

    assert result["value"] == pytest.approx(0.4166666666666667, abs=1e-12)
    # The same double, not one near it: the encoding writes a number so that it reads back exactly.
    assert result["value"] == outcome_consistency.evaluate(tasks).value == derived_value(tasks)
    assert type(result["value"]) is float


def test_an_unavailable_measurement_is_returned_with_its_reason_and_counts_and_nothing_raises():
    result = derived_result(by_task({"once": [1], "also once": [0]}))

    assert (result["state"], result["value"]) == ("unavailable", None)
    assert result["reason"] == "no Task has at least two Attempts with a task outcome"
    assert result["counts"]["tasks"] == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {"reason": "fewer than two Attempts with a task outcome", "count": 2, "ids": ["once", "also once"]}
        ],
    }
    assert result["evidence"] == {"attempt_ids": []}


@pytest.mark.parametrize("tasks", [(), by_task({"once": [1], "also once": [0]})])
def test_the_value_part_of_an_unavailable_measurement_is_a_number_below_the_scale(tasks):
    # The scale is 0 to 1. An evaluator has to return a number, and this one cannot be a value of the metric.
    assert derived_value(tasks) == -1


def test_a_part_past_the_end_of_the_encoded_result_is_zero():
    artifact = derive_artifact("outcome_consistency")
    tasks = by_task({"task": [1, 1, 1, 1, 0]})
    parts = -(-managed_artifact.grade(artifact, tasks, "size") // 6)

    assert managed_artifact.grade(artifact, tasks, str(parts + 1)) == 0


def test_the_derived_artifact_rejects_evidence_in_which_an_attempt_cannot_be_told_from_another():
    tasks = (TaskEvidence("task", tuple(attempts("task", [1, 0]) + attempts("task", [1]))),)

    with pytest.raises(Exception, match="task/attempt-1") as rejection:
        derived_value(tasks)
    assert type(rejection.value).__name__ == "EvidenceError"


def item_of(tasks):
    return derive_artifact("outcome_consistency").item(tasks)


@pytest.mark.parametrize(
    ("item", "problem"),
    [
        ({}, "outcome_consistency_evidence"),
        ({"outcome_consistency_evidence": {"tasks": []}}, "text"),
        ({"outcome_consistency_evidence": "not evidence"}, "altered"),
        # One success turned into a failure after the evidence was encoded: 0.36 would become 0.04.
        (
            managed_artifact.altered_evidence(
                item_of(by_task({"task": [1, 1, 1, 1, 0]})), '"success":true', '"success":false'
            ),
            "altered",
        ),
    ],
)
def test_the_derived_artifact_validates_what_it_is_given(item, problem):
    grader = managed_artifact.load_grader(derive_artifact("outcome_consistency").code_text)

    with pytest.raises(Exception, match=problem) as rejection:
        grader({}, {**item, "result_part": "value"})
    assert type(rejection.value).__name__ == "EvidenceError"


@pytest.mark.parametrize(
    ("asked_for", "problem"),
    [
        ({}, "result_part"),
        ({"result_part": "0"}, "not a part of a result"),
        ({"result_part": "01"}, "not a part of a result"),
        ({"result_part": "reason"}, "not a part of a result"),
        ({"result_part": 1}, "not a part of a result"),
    ],
)
def test_the_derived_artifact_rejects_an_item_that_asks_for_no_part_of_the_result(asked_for, problem):
    grader = managed_artifact.load_grader(derive_artifact("outcome_consistency").code_text)

    with pytest.raises(Exception, match=problem) as rejection:
        grader({}, {**item_of(by_task({"task": [1, 1]})), **asked_for})
    assert type(rejection.value).__name__ == "EvidenceError"


def transported(evidence):
    """An item asking for the plain value, with evidence written here by hand and not by the offering's encoder."""
    return {"outcome_consistency_evidence": managed_artifact.packed(json.dumps(evidence)), "result_part": "value"}


def one_task(*attempts_of_task):
    return {"encoding": 3, "declared": {}, "tasks": [{"task_id": "task", "attempts": list(attempts_of_task)}]}


def attempt(number, **changed):
    """A successful Attempt as the Attempt record writes itself, with some of it changed afterwards."""
    (recorded,) = attempts("task", [1])
    return {**recorded.to_dict(), "attempt_id": f"task/attempt-{number}", **changed}


def test_the_derived_artifact_reads_evidence_that_arrives_intact():
    # Two successes: p = 2/2 and the value is 1.
    grader = managed_artifact.load_grader(derive_artifact("outcome_consistency").code_text)

    assert grader({}, transported(one_task(attempt(1), attempt(2)))) == 1


@pytest.mark.parametrize(
    ("evidence", "problem"),
    [
        (one_task(attempt(1), attempt(2, task_outcome={"success": 0.5, "source": SOURCE})), "binary"),
        (one_task(attempt(1), attempt(2, task_outcome={"success": True, "source": ""})), "source"),
        (one_task(attempt(1), attempt(2, execution_status="finished")), "not an execution status"),
        (one_task(attempt(1), attempt(2, unknown_evidence={"rating": "90"})), "unknown_evidence"),
        (one_task(attempt(1), {"attempt_id": "task/attempt-2", "task_id": "task"}), "recorded without"),
        ({**one_task(attempt(1), attempt(2)), "encoding": 2}, "encoding"),
        ({**one_task(attempt(1), attempt(2)), "declared": {"action_types": None}}, "action_types"),
        ({"encoding": 3, "tasks": [{"task_id": "task", "attempts": [attempt(1), attempt(2)]}]}, "declared"),
    ],
)
def test_the_derived_artifact_rejects_intact_evidence_that_the_evidence_model_does_not_accept(evidence, problem):
    grader = managed_artifact.load_grader(derive_artifact("outcome_consistency").code_text)

    with pytest.raises(Exception, match=problem) as rejection:
        grader({}, transported(evidence))
    assert type(rejection.value).__name__ == "EvidenceError"
