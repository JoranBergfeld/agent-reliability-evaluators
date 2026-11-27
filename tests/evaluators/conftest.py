"""Every Evaluator-seam vector runs on both scoring paths.

Each test in this directory runs twice: against the canonical definition, which is what
customer-process scoring executes, and against the Foundry evaluator derived from it, run
the way the code-evaluator sandbox calls it. The vectors are written once. A vector that is
added later is covered on both paths without anything being done for it, and a change that
makes the two paths differ fails the vector on one of them.

On the second run the `evaluate` of every Evaluator is the derived artifact's: it encodes
the evidence for transport, calls the artifact for every part of the result and puts the
result together (`tests/managed_artifact.py`). What it gives is also compared with what the
canonical definition gives for the same evidence. A test that scores nothing is skipped there.
"""

import json
import sys

import pytest

from reliability_evaluators.evaluators import EVALUATORS
from tests import managed_artifact

CANONICAL_DEFINITION = "canonical definition"
DERIVED_ARTIFACT = "derived artifact"
NOTHING_SCORED = "nothing in this test is scored by an Evaluator, so the derived artifact has nothing to run"


@pytest.fixture(autouse=True, params=[CANONICAL_DEFINITION, DERIVED_ARTIFACT])
def scored_by(request, monkeypatch):
    """What calculates a metric in this test: the canonical definition, or the artifact derived from it."""
    request.node.scored_in_artifact = None
    if request.param == DERIVED_ARTIFACT:
        request.node.scored_in_artifact = False
        for metric_id, evaluator in EVALUATORS.items():
            monkeypatch.setattr(
                sys.modules[evaluator.evaluate.__module__], "evaluate", _recording(request.node, metric_id)
            )
    return request.param


def _recording(node, metric_id):
    canonical = EVALUATORS[metric_id].evaluate
    derived = managed_artifact.evaluate_as_managed(metric_id)

    def recorded(tasks, *declared):
        node.scored_in_artifact = True
        # Evidence that is given as an iterator is read once.
        evidence = (tuple(tasks), *declared)
        in_process, in_artifact = _outcome(canonical, evidence), _outcome(derived, evidence)
        # Beside the expectations of the test itself, which are calculated by hand: the two
        # scoring paths give the same result, to the last digit, or reject with the same error.
        assert _comparable(in_artifact) == _comparable(in_process)
        if isinstance(in_artifact, Exception):
            raise in_artifact
        return in_artifact

    return recorded


def _outcome(evaluate, evidence):
    try:
        return evaluate(*evidence)
    except Exception as raised:
        return raised


def _comparable(outcome):
    if isinstance(outcome, Exception):
        return type(outcome).__name__
    return json.loads(json.dumps(outcome.to_dict(), allow_nan=False))


@pytest.hookimpl(hookwrapper=True)
def pytest_pyfunc_call(pyfuncitem):
    outcome = yield
    if getattr(pyfuncitem, "scored_in_artifact", None) is False and outcome.excinfo is None:
        outcome.force_exception(pytest.skip.Exception(NOTHING_SCORED))
