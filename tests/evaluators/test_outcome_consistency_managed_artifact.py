"""Evaluator seam: the Foundry evaluator derived from the canonical outcome consistency definition.

The vectors are the ones of `test_outcome_consistency.py`, with the same hand-calculated
expectations. Each runs against the canonical definition and against the derived artifact,
so the two scoring paths cannot give different values without a test failing here.

The derived evaluator returns one number for each item it is given. The item names the part of
the result it asks for: the plain value, or a part of the encoded result, which carries the
state, the reason and the counts. `tests/managed_artifact.py` puts the parts together by hand.
"""

import ast
import json

import pytest

from reliability_evaluators.evaluators import outcome_consistency
from reliability_evaluators.evidence import Attempt, ExecutionStatus, TaskEvidence, TaskOutcome, group_by_task
from reliability_evaluators.managed_scoring import derive_artifact
from tests import managed_artifact

SOURCE = "synthetic customer test"


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


def not_completed(status):
    return Attempt(attempt_id="task/attempt-3", task_id="task", execution_status=status)


NO_OUTCOME = Attempt(attempt_id="task/attempt-3", task_id="task", execution_status=ExecutionStatus.COMPLETED)

# Each: grouped evidence, and the value calculated by hand from (2p - 1)^2 per Task, Tasks weighted equally.
AVAILABLE = {
    # p = 4/5, so (2 * 0.8 - 1)^2 = 0.6^2 = 0.36. The companion code's normalization gives 0.
    "four successes in five Attempts": (by_task({"task": [1, 1, 1, 1, 0]}), 0.36),
    "always succeeding": (by_task({"task": [1, 1, 1, 1, 1]}), 1),
    "always failing": (by_task({"task": [0, 0, 0, 0, 0]}), 1),
    # steady 1, mostly (0.5)^2 = 0.25, coin 0: (1 + 0.25 + 0) / 3 = 0.41666... Pooling would give 0.36.
    "Tasks weighted equally": (
        by_task({"steady": [1, 1, 1, 1], "mostly": [1, 1, 1, 0], "coin": [1, 0]}),
        0.4166666666666667,
    ),
    # Only "repeated" is eligible: 0.36, and not (0.36 + 1) / 2 = 0.68.
    "a Task with one Attempt is excluded": (by_task({"repeated": [1, 1, 1, 1, 0], "once": [1]}), 0.36),
    # p = 2/2. Scoring the execution that did not complete as a task failure would give (1/3)^2 = 0.111...
    **{
        f"an execution that {status.value} is not a task failure": (
            group_by_task(attempts("task", [1, 1]) + [not_completed(status)]),
            1,
        )
        for status in (
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.RETRIED,
        )
    },
    # One success and one failure have an outcome: p = 1/2.
    "an Attempt without a task outcome is left out": (group_by_task(attempts("task", [1, 0]) + [NO_OUTCOME]), 0),
}

NOTHING_COUNTED = {"total": 0, "valid": 0, "missing": [], "excluded": []}

# Each: grouped evidence, the reason, and the evidence counts, written out by hand.
UNAVAILABLE = {
    "no Tasks": ((), "no Tasks in the evidence", {"tasks": NOTHING_COUNTED, "attempts": NOTHING_COUNTED}),
    "no eligible Task": (
        by_task({"once": [1], "also once": [0]}),
        "no Task has at least two Attempts with a task outcome",
        {
            "tasks": {
                "total": 2,
                "valid": 0,
                "missing": [],
                "excluded": [
                    {"reason": "fewer than two Attempts with a task outcome", "count": 2, "ids": ["once", "also once"]}
                ],
            },
            "attempts": {
                "total": 2,
                "valid": 0,
                "missing": [],
                "excluded": [
                    {
                        "reason": "its Task has fewer than two Attempts with a task outcome",
                        "count": 2,
                        "ids": ["once/attempt-1", "also once/attempt-1"],
                    }
                ],
            },
        },
    ),
}

REJECTED = {
    "two Attempts with the same identity": (
        (TaskEvidence("task", tuple(attempts("task", [1, 0]) + attempts("task", [1]))),),
        "task/attempt-1",
    ),
    "an Attempt grouped under another Task": (
        (TaskEvidence("task", tuple(attempts("other", [1, 1]))),),
        "other/attempt-1",
    ),
}


def canonical_value(tasks):
    return outcome_consistency.evaluate(tasks).value


def derived_value(tasks):
    return managed_artifact.grade(derive_artifact("outcome_consistency"), tasks, "value")


def derived_result(tasks):
    return managed_artifact.returned_result(derive_artifact("outcome_consistency"), tasks)


@pytest.mark.parametrize("value_of", [canonical_value, derived_value])
@pytest.mark.parametrize("vector", AVAILABLE)
def test_the_derived_artifact_gives_the_value_of_the_canonical_definition(vector, value_of):
    tasks, expected = AVAILABLE[vector]

    value = value_of(tasks)

    assert value == pytest.approx(expected, abs=1e-12)
    assert type(value) is float


@pytest.mark.parametrize("vector", AVAILABLE)
def test_both_paths_compute_the_same_double(vector):
    tasks, _ = AVAILABLE[vector]

    assert derived_value(tasks) == canonical_value(tasks)


@pytest.mark.parametrize("vector", AVAILABLE)
def test_the_encoded_result_carries_the_value_as_the_evaluator_computed_it(vector):
    tasks, expected = AVAILABLE[vector]

    result = derived_result(tasks)

    assert result["state"] == "available"
    assert result["reason"] is None
    assert result["value"] == pytest.approx(expected, abs=1e-12)
    # The same double, not one near it: the encoding writes a number so that it reads back exactly.
    assert result["value"] == canonical_value(tasks)
    assert type(result["value"]) is float


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


@pytest.mark.parametrize("vector", UNAVAILABLE)
def test_an_unavailable_measurement_is_returned_with_its_reason_and_counts_and_nothing_raises(vector):
    tasks, reason, counts = UNAVAILABLE[vector]

    result = derived_result(tasks)

    assert outcome_consistency.evaluate(tasks).reason == reason
    assert (result["state"], result["value"], result["reason"]) == ("unavailable", None, reason)
    assert result["counts"] == counts
    assert result["evidence"] == {"attempt_ids": []}


@pytest.mark.parametrize("vector", UNAVAILABLE)
def test_the_value_part_of_an_unavailable_measurement_is_a_number_below_the_scale(vector):
    tasks, _, _ = UNAVAILABLE[vector]

    # The scale is 0 to 1. An evaluator has to return a number, and this one cannot be a value of the metric.
    assert derived_value(tasks) == -1


def test_a_part_past_the_end_of_the_encoded_result_is_zero():
    artifact = derive_artifact("outcome_consistency")
    tasks = by_task({"task": [1, 1, 1, 1, 0]})
    parts = -(-managed_artifact.grade(artifact, tasks, "size") // 6)

    assert managed_artifact.grade(artifact, tasks, str(parts + 1)) == 0


@pytest.mark.parametrize("vector", REJECTED)
def test_the_derived_artifact_rejects_the_evidence_the_canonical_definition_rejects(vector):
    tasks, named = REJECTED[vector]

    for value_of in (canonical_value, derived_value):
        with pytest.raises(Exception, match=named) as rejection:
            value_of(tasks)
        assert type(rejection.value).__name__ == "EvidenceError"


def item_of(tasks):
    return derive_artifact("outcome_consistency").item(tasks)


def altered(item, old, new):
    """The item with its evidence changed after it was encoded, behind the digest it was encoded with."""
    (field, evidence), = item.items()
    payload = managed_artifact.unpacked(evidence)
    assert old in payload
    return {field: managed_artifact.packed(payload.replace(old, new), digest=evidence.partition(":")[0])}


@pytest.mark.parametrize(
    ("item", "problem"),
    [
        ({}, "outcome_consistency_evidence"),
        ({"outcome_consistency_evidence": {"tasks": []}}, "text"),
        ({"outcome_consistency_evidence": "not evidence"}, "altered"),
        # One success turned into a failure after the evidence was encoded: 0.36 would become 0.04.
        (altered(item_of(by_task({"task": [1, 1, 1, 1, 0]})), '"success":true', '"success":false'), "altered"),
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
    return {"encoding": 2, "tasks": [{"task_id": "task", "attempts": list(attempts_of_task)}]}


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
        ({**one_task(attempt(1), attempt(2)), "encoding": 1}, "encoding"),
    ],
)
def test_the_derived_artifact_rejects_intact_evidence_that_the_evidence_model_does_not_accept(evidence, problem):
    grader = managed_artifact.load_grader(derive_artifact("outcome_consistency").code_text)

    with pytest.raises(Exception, match=problem) as rejection:
        grader({}, transported(evidence))
    assert type(rejection.value).__name__ == "EvidenceError"


# What the Foundry code-evaluator sandbox accepted and refused when observed. See docs/managed_scoring/readme.md.
SANDBOX_MODULES = {
    "__future__", "base64", "dataclasses", "enum", "fractions", "hashlib", "json", "math", "re", "typing", "zlib",
}
# A call by one of these names is refused before the code runs, as a function and as a method: re.compile too.
REFUSED_CALLS = {"compile", "eval", "exec", "globals", "__import__"}
SANDBOX_PYTHON = (3, 12)
CODE_SIZE_LIMIT = 256 * 1024


def test_the_derived_artifact_fits_the_code_evaluator_sandbox():
    code_text = derive_artifact("outcome_consistency").code_text

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
