"""A customer-process rescore of the evidence of a Foundry-managed result, at the customer's request.

The sweep never scores in this process what the customer chose to have scored in Foundry.
This is the one place where that is done, and only because the customer asks for it, for
the metrics they name. The result is recorded beside the Foundry-managed result or failure
of its metric, attributed to this process, and replaces nothing.

Like a recalculation, it works from the earlier scorecard that the customer supplies, which
holds the attempt-level evidence. Nothing here makes an Attempt, invokes an agent, reads a
file or calls a platform.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping, Sequence

from ..evaluators import MetricState, ScoringPath
from ..evidence import EvidenceError
from ..scorecard import RESCORE, AcceptanceCriterion, code_scoring_use, criteria_by_metric, rescore_entry
from .declaration import ActionEvidenceDeclaration, ConfiguredEvaluator
from .recalculation import (
    _cut_short,
    _EarlierScorecard,
    _evidence,
    _read,
    _EarlierEvaluation,
    _submitted_evidence,
    _usage_of_the_evaluator,
    _validating_the_supplied_evidence,
)
from .runner import _scored_in_this_process, _select


class RescoreFailed(Exception):
    """The customer-process rescore did not produce a scorecard.

    The earlier scorecard stands as it was. The message says what stopped the rescore.
    """


def rescore_in_customer_process(
    *,
    earlier_scorecard: Mapping[str, Any],
    metrics: Sequence[str | ConfiguredEvaluator],
    action_evidence: ActionEvidenceDeclaration | None = None,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
) -> dict[str, Any]:
    """Score, in this process, the evidence that Foundry-managed scoring was given for the metrics named.

    This is the customer's explicit request. No sweep and no recalculation does it by
    itself, whatever became of the managed run.

    `earlier_scorecard` is the scorecard of the sweep, as the data of its JSON. It holds the
    attempt-level evidence, so nothing else is needed. It is read and not changed.

    `metrics` names the metrics to rescore, each by its metric identity or as the
    ConfiguredEvaluator the sweep was given. Each has to be a metric that the earlier
    scorecard attributes to Foundry-managed scoring, with a result or with a failure. No
    other metric is rescored.

    The result is a new scorecard that is the earlier one with one addition per rescored
    metric: `customer_process_rescore`, in the entry of that metric. It is a result of its
    own, calculated by the same Evaluator from the Attempts the managed evaluator was given,
    and attributed to the customer-process scoring path. Everything else is as it was. The
    entry of the metric still holds what Foundry-managed scoring gave: the value with its
    fidelity, and with its limitation when it is an unverified result, the unavailable
    measurement with its reason, or the failure with its execution error. A scorecard that was
    partial stays partial and still lists the managed result as missing, and the acceptance
    assessment of the managed result is not changed. The token usage of the Evaluator
    gains the rescore as a scoring of its own, which is code in this process and used no
    tokens. What the managed runs used stays as it was reported, and so do the totals.

    Where the earlier scorecard records the digest of the evidence that was submitted to
    Foundry, the supplied evidence has to give that digest. A rescore of other evidence is
    refused.

    `action_evidence` and `acceptance_criteria` are declared again as for the sweep, because
    the earlier scorecard cannot hold the customer's code. A criterion of a rescored metric
    is assessed on the rescore and reported with it, apart from the assessment of the
    managed result.

    Raises RescoreFailed, and returns no scorecard, when the rescore cannot be done as
    asked: no metric is named, a named metric was not scored by Foundry-managed scoring or
    already has a rescore, the evidence does not validate or is not the evidence that was
    submitted, a declaration is missing, or an acceptance criterion that the earlier
    scorecard assessed for a rescored metric is not declared again.
    """
    try:
        return _rescore(earlier_scorecard, metrics, action_evidence, acceptance_criteria)
    except Exception as error:
        # A ValueError is one of this package's own refusals and reads as a sentence.
        reason = str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}"
        raise RescoreFailed(f"customer-process rescore failed: {reason}") from error


def _rescore(
    earlier_scorecard: Mapping[str, Any],
    metrics: Sequence[str | ConfiguredEvaluator],
    action_evidence: ActionEvidenceDeclaration | None,
    acceptance_criteria: Sequence[AcceptanceCriterion],
) -> dict[str, Any]:
    requested = _requested(metrics)
    criteria = criteria_by_metric(acceptance_criteria)
    with _validating_the_supplied_evidence():
        earlier = _read(earlier_scorecard)
    rescored = deepcopy(dict(earlier_scorecard))
    entries = {entry.get("metric_id"): entry for entry in rescored["metrics"] if isinstance(entry, Mapping)}
    for metric_id, metric in requested.items():
        entry = _managed_entry(entries, metric_id)
        if entry.get("acceptance") is not None and metric_id not in criteria:
            raise ValueError(
                f"the earlier scorecard assessed an acceptance criterion for {metric_id!r}, and none is declared "
                "for the rescore. Declare the acceptance criteria again, as for the sweep"
            )
        with _validating_the_supplied_evidence():
            rescoring = _EarlierEvaluation(
                _select(metric, action_evidence),
                _attempts_used(entry, earlier),
                _cut_short(entry, earlier.executions),
                had_execution_failure=False,
            )
            by_identity = {attempt.attempt_id: attempt for attempt in earlier.attempts}
            evidence = _evidence(rescoring, [by_identity[attempt_id] for attempt_id in rescoring.attempt_ids], earlier)
        scored = _scored_in_this_process(
            rescoring.evaluator, evidence, attempts_used=rescoring.attempt_ids, cut_short=rescoring.cut_short
        )
        same_evidence = _submitted_evidence(entry, evidence, rescoring.evaluator.evaluated_with())
        entry[RESCORE] = rescore_entry(scored, same_evidence, criteria.get(metric_id))
        with _validating_the_supplied_evidence():
            # The rescore is a scoring of its own. It is code in this process, so it used no tokens,
            # and what the managed runs used stays as it was reported.
            _usage_of_the_evaluator(rescored, metric_id)[RESCORE] = code_scoring_use()
    return rescored


def _requested(metrics: Any) -> dict[str, str | ConfiguredEvaluator]:
    """The metrics the customer asks to have rescored, of which there is at least one."""
    if isinstance(metrics, (str, Mapping)) or not isinstance(metrics, Iterable):
        raise ValueError(f"the metrics to rescore are named as a sequence of metric identities. Got {metrics!r}")
    requested: dict[str, str | ConfiguredEvaluator] = {}
    for metric in metrics:
        metric_id = metric.metric_id if isinstance(metric, ConfiguredEvaluator) else metric
        if metric_id in requested:
            raise ValueError(f"{metric_id!r} is named more than once, and a metric has one customer-process rescore")
        requested[metric_id] = metric
    if not requested:
        raise ValueError(
            "no metric was named. A customer-process rescore is made only of the metrics the customer asks for"
        )
    return requested


def _managed_entry(entries: Mapping[Any, Any], metric_id: Any) -> dict[str, Any]:
    """The entry of a metric that Foundry-managed scoring was chosen for, and that has no rescore yet."""
    entry = entries.get(metric_id) if isinstance(metric_id, str) else None
    if entry is None:
        raise ValueError(f"{metric_id!r} is not a reliability metric of the earlier scorecard")
    if entry.get("state") == MetricState.NOT_CONFIGURED.value:
        raise ValueError(f"{metric_id!r} was not selected in the earlier scorecard, so there is nothing to rescore")
    if entry.get("scoring_path") != ScoringPath.FOUNDRY_MANAGED.value:
        raise ValueError(
            f"{metric_id!r} was scored on the scoring path {entry.get('scoring_path')!r} in the earlier scorecard. "
            "A customer-process rescore is made beside a Foundry-managed result or failure, and of no other result"
        )
    if RESCORE in entry:
        raise ValueError(
            f"{metric_id!r} already has a customer-process rescore in the earlier scorecard. "
            "A rescore of the same evidence gives the same result, and the one that is there is not replaced"
        )
    return entry


def _attempts_used(entry: Mapping[str, Any], earlier: _EarlierScorecard) -> tuple[str, ...]:
    """The identities of the Attempts the managed evaluator was given, which are the ones that are rescored."""
    metric_id = entry["metric_id"]
    used = entry.get("attempts_used")
    attempt_ids = used.get("attempt_ids") if isinstance(used, Mapping) else None
    if not isinstance(attempt_ids, list):
        raise EvidenceError(f"the result of {metric_id!r} is recorded without the Attempts its Evaluator was given")
    known = {attempt.attempt_id for attempt in earlier.attempts}
    for attempt_id in attempt_ids:
        if not isinstance(attempt_id, str) or attempt_id not in known:
            raise EvidenceError(
                f"the Evaluator of {metric_id!r} was given Attempt {attempt_id!r}, "
                "which is not in the attempt-level evidence"
            )
    return tuple(attempt_ids)
