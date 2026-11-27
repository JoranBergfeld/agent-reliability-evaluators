"""Foundry-managed scoring: register the derived evaluators, submit grouped evidence, recover the results.

A result recovered here is what Foundry returned. It is never calculated in this process.
When Foundry returns none for a metric, that metric has a failure with its execution error
and nothing is scored in its place. Every metric is scored in a managed run of its own, so
one that fails takes no other metric's result with it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from ..evaluators import MetricResult
from ..evidence import TaskEvidence
from .artifact import DerivationError, ManagedArtifact, derive_artifact
from .foundry import FoundryRequestError

# Registering an evaluator is a preview feature of the project API and has to be opted into.
EVALUATORS_API = "api-version=v1"
PREVIEW_FEATURE = {"Foundry-Features": "Evaluations=V1Preview"}
# One inline row of 1,900,000 characters ran and one of 2,100,000 was rejected when observed
# (decision ticket #12). Evidence is not split over rows: it is refused above what ran.
SUBMITTED_ITEM_LIMIT_CHARACTERS = 1_900_000
OUTPUT_ITEM_PAGE = 100

UNVERIFIED_LIMITATION = (
    "Foundry returned this value. That it equals the value the Evaluator computed there has not been "
    "established, so it is an unverified result: it cannot be met or not met by an acceptance criterion. "
    "Foundry returns only the number, so the evidence counts, the details and the Attempts the value rests "
    "on are not reported on this scoring path. The Attempts the Evaluator was given are."
)
PLATFORM_DIAGNOSTICS_MEANING = (
    "Foundry adds a threshold and a pass or fail label to every result. They are platform defaults, "
    "not the customer's acceptance criterion and not an assessment by the Evaluator."
)
RAISED_IN_FOUNDRY = (
    "A managed evaluator that raises fails the whole run and returns no result. It raises when the metric "
    "is an unavailable measurement for the evidence, which cannot be returned through a managed result in "
    "this version, and when the evidence it received is not the evidence that was submitted"
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

    result: MetricResult
    limitation: str
    provenance: dict[str, Any]
    # What Foundry reported of the token usage of the managed run, as `_reported_usage` gives it.
    reported_usage: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class ManagedFailure:
    """One metric that Foundry-managed scoring gave no result for.

    It is an execution error of the scoring. It says nothing about the agent, and it is
    not an unavailable measurement: nothing was measured.
    """

    # The execution error, in words.
    error: str
    # How far the managed run of the metric got, in the fields of a result's provenance.
    # What was not reached is None.
    provenance: dict[str, Any] = field(
        default_factory=lambda: dict.fromkeys(("evaluator", "eval_id", "run_id", "output_item_id", "evidence_sha256"))
    )
    # What Foundry reported of the token usage of the managed run. None: no run is known.
    reported_usage: dict[str, Any] | None = None


@dataclass
class _ManagedRun:
    """The managed run of one metric, as far as it got."""

    metric_id: str
    evaluator: RegisteredEvaluator | None = None
    # The digest the submitted evidence travels with. The evaluator checks the evidence against it.
    evidence_sha256: str | None = None
    eval_id: str | None = None
    run: dict[str, Any] | None = None
    deadline: float = 0.0
    output_item_id: Any = None

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
        return ManagedFailure(error=error, provenance=provenance, reported_usage=_reported_usage(self.run))


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
        """Score each metric from its grouped Attempt evidence, each in a managed run of its own.

        Returns, for every metric, what Foundry returned for it, or the failure that left it
        without a result. A failure is returned and not raised, and a metric whose run fails
        takes no other metric's result with it: an evaluator that raises fails its whole run,
        so no two metrics share one. The runs are all started before the first is waited for.
        """
        runs = {metric_id: _ManagedRun(metric_id) for metric_id in evidence}
        failures = {
            metric_id: failure
            for metric_id, tasks in evidence.items()
            if (failure := _attempted(runs[metric_id], lambda run: self._submit(run, tasks))) is not None
        }
        return {
            metric_id: failures.get(metric_id) or _attempted(run, self._collect) for metric_id, run in runs.items()
        }

    def _submit(self, run: _ManagedRun, tasks: Sequence[TaskEvidence]) -> None:
        """Register the evaluator of one metric if need be, and start a managed run on its evidence."""
        run.evaluator = self.register([run.metric_id])[run.metric_id]
        item = run.evaluator.artifact.item(tasks)
        run.evidence_sha256 = _evidence_digest(item[run.evaluator.artifact.evidence_field])
        submitted = sum(len(field) + len(text) for field, text in item.items())
        if submitted > SUBMITTED_ITEM_LIMIT_CHARACTERS:
            raise ManagedScoringFailure(
                f"the grouped evidence is {submitted} characters, more than the {SUBMITTED_ITEM_LIMIT_CHARACTERS} "
                "that one managed run was observed to accept. It was not submitted, and nothing was truncated"
            )
        self._start(run, item)

    def _collect(self, run: _ManagedRun) -> ManagedResult:
        """Wait until the managed run of one metric has ended, and recover its result."""
        self._wait(run)
        output_item = self._output_item(run.eval_id, run.run)
        run.output_item_id = output_item.get("id")
        recovered = _recover(run.evaluator, run.eval_id, run.run, output_item)
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

    def _start(self, run: _ManagedRun, item: Mapping[str, str]) -> None:
        """Create the eval of one metric and its run."""
        evaluator = run.evaluator
        name = f"Reliability Evaluators: {run.metric_id}"
        created = self.project.request(
            "POST",
            "/openai/v1/evals",
            {
                "name": name,
                "data_source_config": {
                    "type": "custom",
                    "item_schema": {"type": "object", "properties": {field: {"type": "string"} for field in item}},
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
        run.run = self.project.request(
            "POST",
            f"/openai/v1/evals/{run.eval_id}/runs",
            {
                "name": name,
                "data_source": {"type": "jsonl", "source": {"type": "file_content", "content": [{"item": dict(item)}]}},
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

    def _output_item(self, eval_id: str, run: Mapping[str, Any]) -> dict[str, Any]:
        """The one output item of the run: one item was submitted."""
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
        if len(items) != 1:
            raise ManagedScoringFailure(
                f"the Foundry-managed run {run['id']} of {eval_id} returned {len(items)} output items "
                "for the one item that was submitted"
            )
        return items[0]


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


def _recover(
    evaluator: RegisteredEvaluator, eval_id: str, run: Mapping[str, Any], output_item: Mapping[str, Any]
) -> ManagedResult:
    """Read one metric's result from the output item, or say why there is none."""
    metric_id = evaluator.artifact.metric_id
    where = f"output item {output_item.get('id')!r} of run {run['id']} of {eval_id}"
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
    try:
        # The result contract checks the range of the value: Foundry does not enforce the one it is given.
        result = MetricResult.available(evaluator.artifact.definition, float(score))
    except ValueError as error:
        raise ManagedScoringFailure(
            f"{where} has a score for {metric_id!r} that is not a value of the metric: {error}. It is not reported"
        ) from None
    # The run's link to the Foundry portal is left out: it embeds the customer's subscription identifier.
    provenance = {
        "evaluator": {
            "name": evaluator.name,
            "version": evaluator.version,
            "code_sha256": evaluator.artifact.sha256,
        },
        "eval_id": eval_id,
        "run_id": run["id"],
        "output_item_id": output_item.get("id"),
        "platform_diagnostics": {
            "meaning": PLATFORM_DIAGNOSTICS_MEANING,
            "threshold": returned.get("threshold"),
            "label": returned.get("label"),
            "passed": returned.get("passed"),
        },
    }
    return ManagedResult(
        result=result,
        limitation=UNVERIFIED_LIMITATION,
        provenance=provenance,
        reported_usage=_reported_usage(run, returned),
    )


def _reported_usage(run: Mapping[str, Any] | None, result: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """What Foundry reported of the token usage of one managed run, as it reported it. None: there is no run.

    The usage on the result of the metric's criterion and the per-model rows of the run are
    kept apart and are not interpreted here: they can be two views of the same use, and rows
    were observed to repeat each other's counts (decision ticket #24).
    """
    if run is None:
        return None
    sample = (result or {}).get("sample")
    return {
        "result_sample_usage": sample.get("usage") if isinstance(sample, Mapping) else None,
        "per_model_usage": run.get("per_model_usage"),
    }
