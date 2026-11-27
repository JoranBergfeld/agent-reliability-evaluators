"""Customer acceptance: whether a metric meets the customer's own acceptance criterion.

The offering has no threshold of its own. Without a criterion a metric has no acceptance
assessment at all. An assessment is met, not met, or not assessed with a reason.
"""

from __future__ import annotations

import operator
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Mapping

from ..evaluators import Fidelity, MetricState

COMPARISONS = {
    "at_least": operator.ge,
    "greater_than": operator.gt,
    "at_most": operator.le,
    "less_than": operator.lt,
}


@dataclass(frozen=True)
class CustomerAssessment:
    """The customer's own decision on whether a metric meets their criterion.

    `provenance` says who or what decided, and from what.
    """

    met: bool
    provenance: str


# Receives a copy of the metric's entry in the scorecard.
CustomerAssessor = Callable[[dict[str, Any]], CustomerAssessment]


@dataclass(frozen=True, kw_only=True)
class AcceptanceCriterion:
    """A customer-defined condition on one reliability metric.

    `condition` is JSON-compatible data and is repeated in the scorecard as declared. The
    offering assesses a condition of this form, with either part or both:

        {"value": {"at_least": 0.8}, "minimum_counts": {"tasks": 5}}

    `value` compares the metric value, with any of at_least, greater_than, at_most and
    less_than. `minimum_counts` requires that many valid items of an evidence unit the
    metric counts. Any other condition is not assessed, unless `customer_assessment` is
    given: then the customer's assessor decides and the offering does not.
    """

    metric_id: str
    condition: Any
    customer_assessment: CustomerAssessor | None = None


def assess(criterion: AcceptanceCriterion, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Assess one metric's scorecard entry against the customer's criterion for it."""
    condition = criterion.condition
    try:
        requirements = None if criterion.customer_assessment else _requirements(condition)
        no_verdict = _why_no_verdict_is_possible(entry)
        if no_verdict:
            return _assessment(condition, "not_assessed", reason=no_verdict)
        if criterion.customer_assessment:
            supplied = criterion.customer_assessment(deepcopy(dict(entry)))
            return _assessment(
                condition,
                "met" if supplied.met else "not_met",
                assessed_by="customer",
                provenance=supplied.provenance,
            )
        checks = _checks(requirements, entry)
    except _Unsupported as unsupported:
        return _assessment(condition, "not_assessed", reason=f"unsupported criterion: {unsupported}")
    met = all(check["met"] for check in checks)
    return _assessment(condition, "met" if met else "not_met", assessed_by="offering", checks=checks)


def _assessment(
    condition: Any,
    status: str,
    *,
    reason: str | None = None,
    assessed_by: str | None = None,
    provenance: str | None = None,
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "condition": condition,
        "status": status,
        "reason": reason,
        "assessed_by": assessed_by,
        "provenance": provenance,
        "checks": checks or [],
    }


_STATES_WITHOUT_A_VALUE = {
    MetricState.UNAVAILABLE.value: "an unavailable measurement",
    MetricState.NOT_APPLICABLE.value: "not applicable",
    MetricState.NOT_CONFIGURED.value: "not configured",
}


def _why_no_verdict_is_possible(entry: Mapping[str, Any]) -> str | None:
    """Only an available metric with an authoritative value can be met or not met.

    Everything else, including a state or a fidelity this module does not know, is named
    as the reason for not assessing.
    """
    state = entry["state"]
    if state != MetricState.AVAILABLE.value:
        described = f"the metric is {_STATES_WITHOUT_A_VALUE.get(state, state)}"
        return f"{described}: {entry['reason']}" if entry.get("reason") else described
    if entry.get("fidelity") != Fidelity.AUTHORITATIVE.value:
        return (
            "the value is an unverified result: its fidelity to the evaluator-computed value "
            "has not been established"
        )
    return None


class _Unsupported(Exception):
    """The condition is not one this version can assess."""


@dataclass(frozen=True)
class _Requirements:
    value: tuple[tuple[str, float], ...]
    minimum_counts: tuple[tuple[str, int], ...]


def _requirements(condition: Any) -> _Requirements:
    """Read the condition, or say why it is unsupported.

    Nothing is skipped: a condition with any part that is not understood is unsupported
    as a whole, so that the understood parts cannot pass on their own.
    """
    if not isinstance(condition, Mapping):
        raise _Unsupported(f"{condition!r} is not a condition on 'value' or 'minimum_counts'")
    for part in condition:
        if part not in ("value", "minimum_counts"):
            raise _Unsupported(f"{part!r} is not assessed in this version, only 'value' and 'minimum_counts'")
        if not isinstance(condition[part], Mapping):
            raise _Unsupported(f"{part!r} is {condition[part]!r}, which names no comparison")
    value = tuple(condition.get("value", {}).items())
    minimum_counts = tuple(condition.get("minimum_counts", {}).items())
    for comparison, threshold in value:
        if comparison not in COMPARISONS:
            raise _Unsupported(f"{comparison!r} is not one of {', '.join(COMPARISONS)}")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not isfinite(threshold):
            raise _Unsupported(f"the metric value cannot be compared with {threshold!r}")
    for unit, minimum in minimum_counts:
        if type(minimum) is not int or minimum < 0:
            raise _Unsupported(f"a minimum evidence count is a whole number, not {minimum!r}")
    if not value and not minimum_counts:
        raise _Unsupported("it states no condition on the metric value or on evidence counts")
    return _Requirements(value, minimum_counts)


def _checks(requirements: _Requirements, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks = [_check("value", comparison, threshold, entry["value"]) for comparison, threshold in requirements.value]
    for unit, minimum in requirements.minimum_counts:
        if unit not in entry["counts"]:
            raise _Unsupported(f"the metric reports no evidence count for {unit!r}")
        # `valid` is the evidence the value was calculated from.
        checks.append(_check(f"counts.{unit}.valid", "at_least", minimum, entry["counts"][unit]["valid"]))
    return checks


def _check(subject: str, comparison: str, threshold: Any, observed: Any) -> dict[str, Any]:
    return {
        "subject": subject,
        "comparison": comparison,
        "threshold": threshold,
        "observed": observed,
        "met": COMPARISONS[comparison](observed, threshold),
    }
