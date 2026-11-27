"""Foundry-managed scoring: register the derived evaluators, submit grouped evidence, recover the results.

A result recovered here is what Foundry returned. It is never calculated in this process.
When Foundry returns none for a metric, that metric has a failure with its execution error
and nothing is scored in its place. Every metric is scored in managed runs of its own, so
one that fails takes no other metric's result with it.

A code evaluator returns one number for each submitted row, so a result is asked for in
parts, one row a part. A first run returns the plain metric value and the size of the encoded
result. A second run returns the encoded result, which carries the state of the metric, its
reason, its counts and its details, and the value as the Evaluator computed it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

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
    """Foundry-managed scoring did not produce a result. Nothing was scored in its place.

    It is raised by `register`. `score` does not raise it: it returns a ManagedFailure for
    the metric, so that what the sweep collected is kept.
    """


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
class ManagedFailure:
    """One metric that Foundry-managed scoring gave no result for.

    It is an execution error of the scoring. It says nothing about the agent, and it is
    not an unavailable measurement: nothing was measured.
    """

    # The execution error, in words.
    error: str
    # How far the managed scoring of the metric got, in the fields of a result's provenance.
    # What was not reached is None. The run is the last one that was made.
    provenance: dict[str, Any] = field(
        default_factory=lambda: dict.fromkeys(("evaluator", "eval_id", "run_id", "output_item_id", "evidence_sha256"))
    )


@dataclass
class _ManagedRun:
    """The managed scoring of one metric, as far as it got."""

    metric_id: str
    evaluator: RegisteredEvaluator | None = None
    # The fields every submitted row carries: the grouped evidence of the metric.
    item: dict[str, str] = field(default_factory=dict)
    # The digest the submitted evidence travels with. The evaluator checks the evidence against it.
    evidence_sha256: str | None = None
    eval_id: str | None = None
    # The run that was made last, and until when it is waited for.
    run: dict[str, Any] | None = None
    deadline: float = 0.0
    output_item_id: Any = None
    # What the first run returned: the run, its output item with the plain value, and the size of the encoded result.
    first: dict[str, Any] | None = None
    valued: dict[str, Any] | None = None
    size: Any = None

    def failed(self, error: str) -> ManagedFailure:
        evaluator = self.evaluator
        provenance = {
            "evaluator": None
            if evaluator is None
            else {"name": evaluator.name, "version": evaluator.version, "code_sha256": evaluator.artifact.sha256},
            "eval_id": self.eval_id,
            "run_id": None if self.run is None else self.run.get("id"),
            "output_item_id": self.output_item_id,
            "evidence_sha256": self.evidence_sha256,
        }
        return ManagedFailure(error=error, provenance=provenance)


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

    def score(self, evidence: Mapping[str, Sequence[TaskEvidence]]) -> dict[str, ManagedResult | ManagedFailure]:
        """Score each metric from its grouped Attempt evidence, each in managed runs of its own.

        Returns, for every metric, what Foundry returned for it, or the failure that left it
        without a result. A failure is returned and not raised, and a metric whose run fails
        takes no other metric's result with it: no two metrics share a run.

        A metric takes two runs under one eval. The first returns its plain value and the
        size of its encoded result, the second the encoded result. Each of the two is started
        for every metric before the first of them is waited for.
        """
        runs = {metric_id: _ManagedRun(metric_id) for metric_id in evidence}
        scored: dict[str, ManagedResult | ManagedFailure] = {}
        steps = (lambda run: self._submit(run, evidence[run.metric_id]), self._ask_for_encoded_result, self._collect)
        for step in steps:
            for metric_id, run in runs.items():
                if metric_id not in scored and (ended := _attempted(run, step)) is not None:
                    scored[metric_id] = ended
        return {metric_id: scored[metric_id] for metric_id in evidence}

    def _submit(self, run: _ManagedRun, tasks: Sequence[TaskEvidence]) -> None:
        """Register the evaluator of one metric if need be, and start the run for its value and the size of its result."""
        run.evaluator = self.register([run.metric_id])[run.metric_id]
        run.item = run.evaluator.artifact.item(tasks)
        run.evidence_sha256 = _evidence_digest(run.item[run.evaluator.artifact.evidence_field])
        self._create_eval(run)
        self._start(run, "value and size", [grader.VALUE_PART, grader.SIZE_PART])

    def _ask_for_encoded_result(self, run: _ManagedRun) -> None:
        """Wait for the first run of one metric, and start the run for every part of its encoded result."""
        self._wait(run)
        run.first = run.run
        run.valued, sized = self._output_items(run, 2)
        run.output_item_id = run.valued.get("id")
        _returned(run.metric_id, run.valued, _where(run.eval_id, run.first, run.valued))
        where = _where(run.eval_id, run.first, sized)
        run.size = _returned(run.metric_id, sized, where)["score"]
        try:
            parts = grader.result_parts(run.size)
        except grader.UnreadableResult as error:
            raise ManagedScoringFailure(f"{where}, for {run.metric_id!r}: {error}") from None
        self._start(run, "encoded result", [str(number) for number in range(1, parts + 1)])

    def _collect(self, run: _ManagedRun) -> ManagedResult:
        """Wait until the second run of one metric has ended, and recover its result."""
        self._wait(run)
        encoded = self._output_items(run, grader.result_parts(run.size))
        recovered = _recover(run, encoded)
        return replace(recovered, provenance={**recovered.provenance, "evidence_sha256": run.evidence_sha256})

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

    def _create_eval(self, run: _ManagedRun) -> None:
        evaluator = run.evaluator
        created = self.project.request(
            "POST",
            "/openai/v1/evals",
            {
                "name": f"Reliability Evaluators: {run.metric_id}",
                "data_source_config": {
                    "type": "custom",
                    "item_schema": {
                        "type": "object",
                        "properties": {field: {"type": "string"} for field in (*run.item, grader.RESULT_PART)},
                    },
                    "include_sample_schema": True,
                },
                "testing_criteria": [
                    {
                        "type": "azure_ai_evaluator",
                        # A result is matched to its metric by this name.
                        "name": run.metric_id,
                        "evaluator_name": evaluator.name,
                        "evaluator_version": evaluator.version,
                        "initialization_parameters": {},
                    }
                ],
            },
        )
        run.eval_id = created["id"]

    def _start(self, run: _ManagedRun, asked_for: str, parts: Sequence[str]) -> None:
        """Start a run of the metric's eval with one row for each part of the result that is asked for.

        Every row carries the evidence, because a row is graded on its own.
        """
        rows = [{**run.item, grader.RESULT_PART: part} for part in parts]
        submitted = sum(len(field) + len(text) for row in rows for field, text in row.items())
        if submitted > SUBMITTED_RUN_LIMIT_CHARACTERS:
            raise ManagedScoringFailure(
                f"the run for the {asked_for} would submit {submitted} characters in {len(rows)} rows, each with "
                f"the grouped evidence, more than the {SUBMITTED_RUN_LIMIT_CHARACTERS} that one managed run was "
                "observed to accept. It was not submitted, and nothing was truncated"
            )
        run.run = self.project.request(
            "POST",
            f"/openai/v1/evals/{run.eval_id}/runs",
            {
                "name": f"Reliability Evaluators: {run.metric_id}, {asked_for}",
                "data_source": {"type": "jsonl", "source": {"type": "file_content", "content": [{"item": row} for row in rows]}},
            },
        )
        run.deadline = time.monotonic() + self.run_timeout_seconds

    def _wait(self, run: _ManagedRun) -> None:
        """Wait until the run has ended. One that did not complete gave no result."""
        eval_id, run_id = run.eval_id, run.run["id"]
        run_path = f"/openai/v1/evals/{eval_id}/runs/{run_id}"
        while run.run.get("status") not in ("completed", "failed", "canceled"):
            if time.monotonic() >= run.deadline:
                self._cancel(run_path)
                raise ManagedScoringFailure(
                    f"the Foundry-managed run {run_id} of {eval_id} had not ended after "
                    f"{self.run_timeout_seconds:g} seconds, in status {run.run.get('status')!r}. "
                    "Its cancellation was requested"
                )
            time.sleep(self.poll_interval_seconds)
            run.run = self.project.request("GET", run_path)
        if run.run["status"] != "completed":
            error = run.run.get("error") or {}
            reported = f"{error.get('code')}: {error.get('message')}".rstrip(". ")
            raise ManagedScoringFailure(
                f"the Foundry-managed run {run_id} of {eval_id} ended as {run.run['status']}: {reported}. "
                f"{RAISED_IN_FOUNDRY}"
            )

    def _cancel(self, run_path: str) -> None:
        try:
            self.project.request("POST", f"{run_path}/cancel")
        except FoundryRequestError:
            # The timeout is what the caller is told. A run that could not be cancelled ends by itself.
            pass

    def _output_items(self, run: _ManagedRun, submitted: int) -> list[dict[str, Any]]:
        """The output items of the last run in the order of the rows that were submitted, one for each row."""
        eval_id, run_id = run.eval_id, run.run["id"]
        items: list[dict[str, Any]] = []
        after = ""
        while True:
            page = self.project.request(
                "GET", f"/openai/v1/evals/{eval_id}/runs/{run_id}/output_items?limit={OUTPUT_ITEM_PAGE}{after}"
            )
            items.extend(page.get("data", []))
            if not page.get("has_more"):
                break
            after = f"&after={page['last_id']}"
        # An item names the row it is for by the position of the row in what was submitted.
        by_row = {item.get("datasource_item_id"): item for item in items}
        if len(items) != submitted or set(by_row) != set(range(submitted)):
            raise ManagedScoringFailure(
                f"the Foundry-managed run {run_id} of {eval_id} returned {len(items)} output items "
                f"for the {submitted} rows that were submitted, or not one for each row"
            )
        return [by_row[row] for row in range(submitted)]


def _attempted(run: _ManagedRun, step: Callable[[_ManagedRun], Any]) -> Any:
    """What a step of the managed run of one metric gives, or the failure of that metric when it fails.

    Every error is that metric's failure, whatever it is: the Attempts were made by now, and
    what was collected is not lost to an answer of the project that was not expected.
    """
    try:
        return step(run)
    except ManagedScoringFailure as failure:
        return run.failed(str(failure))
    except FoundryRequestError as error:
        return run.failed(f"the Foundry-managed run could not be made or read: {error}")
    except Exception as error:
        return run.failed(f"Foundry-managed scoring stopped on {type(error).__name__}: {error}")


def _evidence_digest(encoded: str) -> str:
    """The digest that encoded evidence travels with."""
    return encoded.partition(":")[0]


def submitted_evidence_sha256(metric_id: str, tasks: Iterable[TaskEvidence]) -> str:
    """The digest that the grouped Attempt evidence of a metric is submitted to Foundry with.

    A managed result records it. Evidence that gives the same digest is the evidence the
    managed evaluator was sent. Raises DerivationError for a metric without a Foundry evaluator.
    """
    artifact = derive_artifact(metric_id)
    return _evidence_digest(artifact.item(tasks)[artifact.evidence_field])


def _artifact(metric_id: str) -> ManagedArtifact:
    try:
        return derive_artifact(metric_id)
    except DerivationError as error:
        raise ManagedScoringFailure(str(error)) from error


def _where(eval_id: str, run: Mapping[str, Any], output_item: Mapping[str, Any]) -> str:
    return f"output item {output_item.get('id')!r} of run {run['id']} of {eval_id}"


def _returned(metric_id: str, output_item: Mapping[str, Any], where: str) -> Mapping[str, Any]:
    """What Foundry returned for the metric on one row: a completed result with a number as its score."""
    # A completed run and a completed item do not show that the evaluator ran: the errors do.
    sample_error = (output_item.get("sample") or {}).get("error")
    if sample_error:
        raise ManagedScoringFailure(f"{where} carries an error: {sample_error}")
    results = [result for result in output_item.get("results", []) if result.get("name") == metric_id]
    if len(results) != 1:
        raise ManagedScoringFailure(f"{where} has {len(results)} results for {metric_id!r}, and one was expected")
    (returned,) = results
    if returned.get("status") != "completed":
        # An evaluator that raised on this item leaves its error on the result, not on the item.
        result_error = (returned.get("sample") or {}).get("error")
        raise ManagedScoringFailure(
            f"{where} has a result for {metric_id!r} in status {returned.get('status')!r}"
            + (f": {result_error}" if result_error else "")
        )
    score = returned.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ManagedScoringFailure(f"{where} has {score!r} as the score of {metric_id!r}, which is not a number")
    return returned


def _recover(run: _ManagedRun, encoded: Sequence[Mapping[str, Any]]) -> ManagedResult:
    """Read the metric's result from what its two runs returned, or say why there is none."""
    evaluator, eval_id, metric_id = run.evaluator, run.eval_id, run.metric_id
    artifact = evaluator.artifact
    where = _where(eval_id, run.first, run.valued)
    returned = _returned(metric_id, run.valued, where)
    parts = [_returned(metric_id, part, _where(eval_id, run.run, part))["score"] for part in encoded]
    try:
        computed = grader.decode_result(artifact.definition, run.item[artifact.evidence_field], run.size, parts)
    except grader.UnreadableResult as error:
        raise ManagedScoringFailure(
            f"run {run.run['id']} of {eval_id} did not return a result of {metric_id!r} that can be read: {error}. "
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
        "run_id": run.first["id"],
        "output_item_id": run.valued.get("id"),
        "encoded_result": {"run_id": run.run["id"], "bytes": int(run.size), "parts": len(encoded)},
    }
    score = returned["score"]
    if computed.state is not MetricState.AVAILABLE:
        # The state says the metric has no value. The number in the value part is then the internal
        # marker for that. It is checked, and it is never reported.
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
