"""Foundry-managed scoring: register the derived evaluators, submit grouped evidence, recover the results.

A result recovered here is what Foundry returned. It is never calculated in this process,
and when Foundry returns none, the failure is raised and nothing is scored in its place.

A code evaluator returns one number for each submitted row, so a result is asked for in
parts, one row a part. A first run returns the plain metric value and the size of the encoded
result. A second run returns the encoded result, which carries the state of the metric, its
reason, its counts and its details, and the value as the Evaluator computed it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Protocol, Sequence

from ..evaluators import MetricResult, MetricState
from ..evidence import TaskEvidence
from . import grader
from .artifact import DerivationError, ManagedArtifact, derive_artifact
from .foundry import FoundryRequestError

# Registering an evaluator is a preview feature of the project API and has to be opted into.
EVALUATORS_API = "api-version=v1"
PREVIEW_FEATURE = {"Foundry-Features": "Evaluations=V1Preview"}
# The rows of one run are submitted inline, and the limit is on all of them together: one
# row of 1,900,000 characters ran and one of 2,100,000 was rejected (decision ticket #12),
# and three rows of 1,000,000 were rejected where ten of 100,000 ran. Every row carries the
# evidence, so a run that would be larger than what ran is refused, and nothing is truncated.
SUBMITTED_RUN_LIMIT_CHARACTERS = 1_900_000
OUTPUT_ITEM_PAGE = 100

UNVERIFIED_LIMITATION = (
    "Foundry returned this value. That it equals the value the Evaluator computed there has not been "
    "established, so it is an unverified result: it cannot be met or not met by an acceptance criterion. "
    "The evidence counts, the details and the Attempts the value rests on are the ones the Evaluator "
    "returned from Foundry with it, as an encoded result."
)
NO_VALUE_LIMITATION = (
    "Foundry returned this result, and it has no value: the Evaluator found the metric to be {state} for "
    "the evidence, and returned that state, its reason and its evidence counts as an encoded result. "
    "No fidelity rule yet establishes that what is received from Foundry is what the Evaluator computed "
    "there, so the result is reported as unverified."
)
PLATFORM_DIAGNOSTICS_MEANING = (
    "Foundry adds a threshold and a pass or fail label to every result. They are platform defaults, "
    "not the customer's acceptance criterion and not an assessment by the Evaluator."
)
RAISED_IN_FOUNDRY = (
    "A run in which the managed evaluator raises on every row fails as a whole and returns no result. "
    "The evaluator raises when the evidence it received is not the evidence that was submitted. It does "
    "not raise for an unavailable measurement"
)


class ManagedScoringFailure(Exception):
    """Foundry-managed scoring did not produce a result. Nothing was scored in its place."""


class Project(Protocol):
    """What managed scoring needs of a Foundry project. `FoundryProject` is the real one."""

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any: ...


@dataclass(frozen=True, kw_only=True)
class RegisteredEvaluator:
    """A derived evaluator as it is registered in the customer's project."""

    artifact: ManagedArtifact
    version: str
    # False when the project already held a version with exactly this code.
    newly_registered: bool

    @property
    def name(self) -> str:
        return self.artifact.evaluator_name


@dataclass(frozen=True, kw_only=True)
class ManagedResult:
    """One metric as Foundry returned it, with why it is unverified and where it came from."""

    # The result as it is reported. Its value is the number Foundry returned as the score.
    result: MetricResult
    limitation: str
    provenance: dict[str, Any]
    # The value the Evaluator computed, as the encoded result carried it, or None when the
    # metric has no value. Nothing compares it with the returned score: no fidelity rule exists yet.
    computed_value: float | None = None


@dataclass(frozen=True, kw_only=True)
class FoundryManagedScoring:
    """The customer's choice to score in Foundry: their project, and how long to wait for a run.

    Everything is done in that project, as the identity the project is reached with.
    """

    project: Project
    poll_interval_seconds: float = 10.0
    run_timeout_seconds: float = 900.0

    def register(self, metric_ids: Iterable[str]) -> dict[str, RegisteredEvaluator]:
        """Make sure the project holds the derived evaluator of each metric, and say which version it is.

        Idempotent: a version that already has exactly the derived code is used as it is. A
        new version is registered only when the project has none with that code, so an
        evaluator of the same name with other code is never run in its place.
        """
        try:
            return {metric_id: self._register(_artifact(metric_id)) for metric_id in metric_ids}
        except FoundryRequestError as error:
            raise ManagedScoringFailure(f"the derived evaluator could not be registered in the Foundry project: {error}") from error

    def score(self, evidence: Mapping[str, Sequence[TaskEvidence]]) -> dict[str, ManagedResult]:
        """Score each metric from its grouped Attempt evidence in two managed runs under one eval.

        The first run returns the plain value of each metric and the size of its encoded
        result, the second the encoded results. Returns what Foundry returned for every
        metric, or raises ManagedScoringFailure.
        """
        registered = self.register(evidence)
        item: dict[str, str] = {}
        for metric_id, tasks in evidence.items():
            item.update(registered[metric_id].artifact.item(tasks))
        try:
            eval_id = self._create_eval(registered, [*item, grader.RESULT_PART])
            first = self._run(eval_id, "value and size", _rows(item, [grader.VALUE_PART, grader.SIZE_PART]))
            valued, sized = self._output_items(eval_id, first, 2)
            sizes = {metric_id: _returned(metric_id, sized, _where(eval_id, first, sized))["score"] for metric_id in evidence}
            parts = max(_parts(metric_id, size, _where(eval_id, first, sized)) for metric_id, size in sizes.items())
            second = self._run(eval_id, "encoded result", _rows(item, [str(number) for number in range(1, parts + 1)]))
            encoded = self._output_items(eval_id, second, parts)
        except FoundryRequestError as error:
            raise ManagedScoringFailure(f"the Foundry-managed run could not be made or read: {error}") from error
        return {
            metric_id: _recover(
                registered[metric_id], item, eval_id, first, valued, sizes[metric_id], second, encoded
            )
            for metric_id in evidence
        }

    def _register(self, artifact: ManagedArtifact) -> RegisteredEvaluator:
        path = f"/evaluators/{artifact.evaluator_name}/versions?{EVALUATORS_API}"
        while path:
            page = self.project.request("GET", path)
            for held in page.get("value", []):
                if (held.get("definition") or {}).get("code_text") == artifact.code_text:
                    return RegisteredEvaluator(artifact=artifact, version=str(held["version"]), newly_registered=False)
            path = page.get("nextLink")
        created = self.project.request(
            "POST", f"/evaluators/{artifact.evaluator_name}/versions?{EVALUATORS_API}", artifact.registration(), PREVIEW_FEATURE
        )
        return RegisteredEvaluator(artifact=artifact, version=str(created["version"]), newly_registered=True)

    def _create_eval(self, registered: Mapping[str, RegisteredEvaluator], fields: Sequence[str]) -> str:
        created = self.project.request(
            "POST",
            "/openai/v1/evals",
            {
                "name": "Reliability Evaluators: " + ", ".join(registered),
                "data_source_config": {
                    "type": "custom",
                    "item_schema": {"type": "object", "properties": {field: {"type": "string"} for field in fields}},
                    "include_sample_schema": True,
                },
                "testing_criteria": [
                    {
                        "type": "azure_ai_evaluator",
                        # A result is matched to its metric by this name.
                        "name": metric_id,
                        "evaluator_name": evaluator.name,
                        "evaluator_version": evaluator.version,
                        "initialization_parameters": {},
                    }
                    for metric_id, evaluator in registered.items()
                ],
            },
        )
        return created["id"]

    def _run(self, eval_id: str, name: str, rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
        """Submit the rows to one run of the eval, and wait until the run has ended."""
        submitted = sum(len(field) + len(text) for row in rows for field, text in row.items())
        if submitted > SUBMITTED_RUN_LIMIT_CHARACTERS:
            raise ManagedScoringFailure(
                f"the run for the {name} would submit {submitted} characters in {len(rows)} rows, each with the "
                f"grouped evidence, more than the {SUBMITTED_RUN_LIMIT_CHARACTERS} that one managed run was "
                "observed to accept. It was not submitted, and nothing was truncated"
            )
        run = self.project.request(
            "POST",
            f"/openai/v1/evals/{eval_id}/runs",
            {
                "name": f"Reliability Evaluators: {name}",
                "data_source": {
                    "type": "jsonl",
                    "source": {"type": "file_content", "content": [{"item": dict(row)} for row in rows]},
                },
            },
        )
        run_path = f"/openai/v1/evals/{eval_id}/runs/{run['id']}"
        deadline = time.monotonic() + self.run_timeout_seconds
        while run.get("status") not in ("completed", "failed", "canceled"):
            if time.monotonic() >= deadline:
                self._cancel(run_path)
                raise ManagedScoringFailure(
                    f"the Foundry-managed run {run['id']} of {eval_id} had not ended after "
                    f"{self.run_timeout_seconds:g} seconds, in status {run.get('status')!r}. Its cancellation was requested"
                )
            time.sleep(self.poll_interval_seconds)
            run = self.project.request("GET", run_path)
        if run["status"] != "completed":
            error = run.get("error") or {}
            reported = f"{error.get('code')}: {error.get('message')}".rstrip(". ")
            raise ManagedScoringFailure(
                f"the Foundry-managed run {run['id']} of {eval_id} ended as {run['status']}: {reported}. "
                f"{RAISED_IN_FOUNDRY}"
            )
        return run

    def _cancel(self, run_path: str) -> None:
        try:
            self.project.request("POST", f"{run_path}/cancel")
        except FoundryRequestError:
            # The timeout is what the caller is told. A run that could not be cancelled ends by itself.
            pass

    def _output_items(self, eval_id: str, run: Mapping[str, Any], submitted: int) -> list[dict[str, Any]]:
        """The output items of the run in the order of the rows that were submitted, one for each row."""
        items: list[dict[str, Any]] = []
        after = ""
        while True:
            page = self.project.request(
                "GET", f"/openai/v1/evals/{eval_id}/runs/{run['id']}/output_items?limit={OUTPUT_ITEM_PAGE}{after}"
            )
            items.extend(page.get("data", []))
            if not page.get("has_more"):
                break
            after = f"&after={page['last_id']}"
        # An item names the row it is for by the position of the row in what was submitted.
        by_row = {item.get("datasource_item_id"): item for item in items}
        if len(items) != submitted or set(by_row) != set(range(submitted)):
            raise ManagedScoringFailure(
                f"the Foundry-managed run {run['id']} of {eval_id} returned {len(items)} output items "
                f"for the {submitted} rows that were submitted, or not one for each row"
            )
        return [by_row[row] for row in range(submitted)]


def _artifact(metric_id: str) -> ManagedArtifact:
    try:
        return derive_artifact(metric_id)
    except DerivationError as error:
        raise ManagedScoringFailure(str(error)) from error


def _rows(item: Mapping[str, str], parts: Sequence[str]) -> list[dict[str, str]]:
    """One row for each part that is asked for. Every row carries the evidence: a row is graded on its own."""
    return [{**item, grader.RESULT_PART: part} for part in parts]


def _where(eval_id: str, run: Mapping[str, Any], output_item: Mapping[str, Any]) -> str:
    return f"output item {output_item.get('id')!r} of run {run['id']} of {eval_id}"


def _returned(metric_id: str, output_item: Mapping[str, Any], where: str) -> Mapping[str, Any]:
    """What Foundry returned for one metric on one row: a completed result with a number as its score."""
    # A completed run and a completed item do not show that the evaluator ran: the errors do.
    sample_error = (output_item.get("sample") or {}).get("error")
    if sample_error:
        raise ManagedScoringFailure(f"{where} carries an error: {sample_error}")
    results = [result for result in output_item.get("results", []) if result.get("name") == metric_id]
    if len(results) != 1:
        raise ManagedScoringFailure(f"{where} has {len(results)} results for {metric_id!r}, and one was expected")
    (returned,) = results
    if returned.get("status") != "completed":
        error = (returned.get("sample") or {}).get("error")
        raise ManagedScoringFailure(
            f"{where} has a result for {metric_id!r} in status {returned.get('status')!r}" + (f": {error}" if error else "")
        )
    score = returned.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ManagedScoringFailure(f"{where} has {score!r} as the score of {metric_id!r}, which is not a number")
    return returned


def _parts(metric_id: str, size: Any, where: str) -> int:
    try:
        return grader.result_parts(size)
    except grader.UnreadableResult as error:
        raise ManagedScoringFailure(f"{where}, for {metric_id!r}: {error}") from None


def _recover(
    evaluator: RegisteredEvaluator,
    item: Mapping[str, str],
    eval_id: str,
    first: Mapping[str, Any],
    valued: Mapping[str, Any],
    size: Any,
    second: Mapping[str, Any],
    encoded: Sequence[Mapping[str, Any]],
) -> ManagedResult:
    """Read one metric's result from what the two runs returned, or say why there is none."""
    artifact = evaluator.artifact
    metric_id = artifact.metric_id
    where = _where(eval_id, first, valued)
    returned = _returned(metric_id, valued, where)
    parts = [_returned(metric_id, part, _where(eval_id, second, part))["score"] for part in encoded]
    try:
        computed = grader.decode_result(artifact.definition, item[artifact.evidence_field], size, parts)
    except grader.UnreadableResult as error:
        raise ManagedScoringFailure(
            f"run {second['id']} of {eval_id} did not return a result of {metric_id!r} that can be read: {error}. "
            "Nothing is reported in its place"
        ) from None
    # The run's link to the Foundry portal is left out: it embeds the customer's subscription identifier.
    provenance = {
        "evaluator": {
            "name": evaluator.name,
            "version": evaluator.version,
            "code_sha256": artifact.sha256,
        },
        "eval_id": eval_id,
        "run_id": first["id"],
        "output_item_id": valued.get("id"),
        "encoded_result": {"run_id": second["id"], "bytes": int(size), "parts": grader.result_parts(size)},
    }
    score = returned["score"]
    if computed.state is not MetricState.AVAILABLE:
        # The state says the metric has no value. The number in the value part is then the internal
        # marker for that, and it is checked and never reported.
        if score != grader.no_value_marker(artifact.definition):
            raise ManagedScoringFailure(
                f"{where} has {score!r} as the value of {metric_id!r}, where the encoded result says the metric "
                f"is {computed.state.value} and has no value. The two do not agree, so nothing is reported"
            )
        limitation = NO_VALUE_LIMITATION.format(state=_STATES_WITHOUT_A_VALUE[computed.state])
        return ManagedResult(result=computed, limitation=limitation, provenance=provenance)
    try:
        # The result contract checks the range of the value: Foundry does not enforce the one it is given.
        result = replace(computed, value=float(score))
    except ValueError as error:
        raise ManagedScoringFailure(
            f"{where} has a score for {metric_id!r} that is not a value of the metric: {error}. It is not reported"
        ) from None
    provenance["platform_diagnostics"] = {
        "meaning": PLATFORM_DIAGNOSTICS_MEANING,
        "threshold": returned.get("threshold"),
        "label": returned.get("label"),
        "passed": returned.get("passed"),
    }
    return ManagedResult(
        result=result, limitation=UNVERIFIED_LIMITATION, provenance=provenance, computed_value=computed.value
    )


_STATES_WITHOUT_A_VALUE = {
    MetricState.UNAVAILABLE: "an unavailable measurement",
    MetricState.NOT_APPLICABLE: "not applicable",
}
