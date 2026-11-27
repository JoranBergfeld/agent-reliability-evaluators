"""Foundry-managed scoring: register the derived evaluators, submit grouped evidence, recover the results.

A result recovered here is what Foundry returned. It is never calculated in this process,
and when Foundry returns none, the failure is raised and nothing is scored in its place.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence

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

    result: MetricResult
    limitation: str
    provenance: dict[str, Any]


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
        """Score each metric from its grouped Attempt evidence in one managed run.

        Returns what Foundry returned for every metric, or raises ManagedScoringFailure.
        """
        registered = self.register(evidence)
        item: dict[str, str] = {}
        for metric_id, tasks in evidence.items():
            item.update(registered[metric_id].artifact.item(tasks))
        submitted = sum(len(field) + len(text) for field, text in item.items())
        if submitted > SUBMITTED_ITEM_LIMIT_CHARACTERS:
            raise ManagedScoringFailure(
                f"the grouped evidence is {submitted} characters, more than the {SUBMITTED_ITEM_LIMIT_CHARACTERS} "
                "that one managed run was observed to accept. It was not submitted, and nothing was truncated"
            )
        try:
            eval_id, run = self._run(registered, item)
            output_item = self._output_item(eval_id, run)
        except FoundryRequestError as error:
            raise ManagedScoringFailure(f"the Foundry-managed run could not be made or read: {error}") from error
        return {
            metric_id: _recover(registered[metric_id], eval_id, run, output_item) for metric_id in evidence
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

    def _run(self, registered: Mapping[str, RegisteredEvaluator], item: Mapping[str, str]) -> tuple[str, dict[str, Any]]:
        """Create the eval and its run, and wait until the run has ended."""
        name = "Reliability Evaluators: " + ", ".join(registered)
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
                        "name": metric_id,
                        "evaluator_name": evaluator.name,
                        "evaluator_version": evaluator.version,
                        "initialization_parameters": {},
                    }
                    for metric_id, evaluator in registered.items()
                ],
            },
        )
        eval_id = created["id"]
        run = self.project.request(
            "POST",
            f"/openai/v1/evals/{eval_id}/runs",
            {
                "name": name,
                "data_source": {"type": "jsonl", "source": {"type": "file_content", "content": [{"item": dict(item)}]}},
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
        return eval_id, run

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
        raise ManagedScoringFailure(f"{where} has a result for {metric_id!r} in status {returned.get('status')!r}")
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
    return ManagedResult(result=result, limitation=UNVERIFIED_LIMITATION, provenance=provenance)
