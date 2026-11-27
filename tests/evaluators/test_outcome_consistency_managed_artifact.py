"""Evaluator seam: the Foundry evaluator derived from the canonical outcome consistency definition.

The vectors are the ones of `test_outcome_consistency.py`, with the same hand-calculated
expectations. Each runs against the canonical definition and against the derived artifact,
so the two scoring paths cannot give different values without a test failing here.
"""

import ast
import hashlib
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


FAILED_EXECUTION = Attempt(attempt_id="task/attempt-3", task_id="task", execution_status=ExecutionStatus.FAILED)
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
    # p = 2/2. Scoring the failed execution as a task failure would give (1/3)^2 = 0.111...
    "a failed execution is not a task failure": (group_by_task(attempts("task", [1, 1]) + [FAILED_EXECUTION]), 1),
    # One success and one failure have an outcome: p = 1/2.
    "an Attempt without a task outcome is left out": (group_by_task(attempts("task", [1, 0]) + [NO_OUTCOME]), 0),
}

UNAVAILABLE = {
    "no Tasks": ((), "no Tasks in the evidence"),
    "no eligible Task": (
        by_task({"once": [1], "also once": [0]}),
        "no Task has at least two Attempts with a task outcome",
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
    return managed_artifact.grade(derive_artifact("outcome_consistency"), tasks)


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


@pytest.mark.parametrize("vector", UNAVAILABLE)
def test_an_unavailable_measurement_is_never_returned_as_a_number(vector):
    tasks, reason = UNAVAILABLE[vector]

    assert outcome_consistency.evaluate(tasks).reason == reason
    # The artifact has no way yet to return the state, so it fails with the reason and returns nothing.
    with pytest.raises(Exception, match=reason) as failure:
        derived_value(tasks)
    assert type(failure.value).__name__ == "UnavailableThroughManagedScoring"


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
    (field, text), = item.items()
    assert old in text
    return {field: text.replace(old, new)}


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
        grader({}, item)
    assert type(rejection.value).__name__ == "EvidenceError"


def transported(evidence):
    """Evidence as it travels, written here by hand and not by the offering's encoder."""
    payload = json.dumps(evidence)
    return {"outcome_consistency_evidence": hashlib.sha256(payload.encode("utf-8")).hexdigest() + ":" + payload}


def one_task(*attempts_of_task):
    return {"encoding": 1, "tasks": [{"task_id": "task", "attempts": list(attempts_of_task)}]}


def attempt(number, **evidence):
    recorded = {"execution_status": "completed", "task_outcome": {"success": True, "source": SOURCE}, **evidence}
    return {"attempt_id": f"task/attempt-{number}", "task_id": "task", **recorded}


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
        (one_task(attempt(1), attempt(2, confidence={"rating": "90"})), "cannot be read back"),
        ({**one_task(attempt(1), attempt(2)), "encoding": 2}, "encoding"),
    ],
)
def test_the_derived_artifact_rejects_intact_evidence_that_the_evidence_model_does_not_accept(evidence, problem):
    grader = managed_artifact.load_grader(derive_artifact("outcome_consistency").code_text)

    with pytest.raises(Exception, match=problem) as rejection:
        grader({}, transported(evidence))
    assert type(rejection.value).__name__ == "EvidenceError"


# What the Foundry code-evaluator sandbox accepted and refused when observed. See docs/managed_scoring/readme.md.
SANDBOX_MODULES = {"__future__", "dataclasses", "enum", "fractions", "hashlib", "json", "math", "re", "typing"}
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
