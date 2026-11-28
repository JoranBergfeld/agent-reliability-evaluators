"""Agent target that invokes a Foundry agent through Foundry cloud evaluation: one evaluation run per Attempt.

Standard library only: the requests are sent by the customer's `FoundryProject`, the same one
Foundry-managed scoring uses. See docs/foundry_cloud_evaluation/readme.md.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Iterator, Protocol

from ..evidence import ActionEvent, ResourceMeasurement, ResourceType
from ..foundry import AgentDetails, read_agent_details
from ..managed_scoring.foundry import FoundryRequestError
from .foundry_hosted import FoundryHostedAgentTarget, output_items, output_text
from .foundry_prompt_agent import TOOL_CHOICE_NONE, failure_of_a_response, not_isolated, tool_calls_of_a_response
from .foundry_prompt_agent import USAGE_SOURCE as USAGE_SOURCE_OF_A_RESPONSE
from .interface import (
    CUSTOMER_CODE,
    FOUNDRY_CLOUD_EVALUATION,
    AgentResponse,
    AttemptRequest,
    ConfidenceRequest,
    ConfidenceResponse,
    InvocationFailed,
    IsolatedFollowUp,
)

API_VERSION = "v1"
AGENT_TARGET = "foundry_cloud_evaluation"
RESPONSES_PROTOCOL = "responses"
# Where a follow-up to a prompt agent is sent: the project's own Responses route, not an evaluation run.
RESPONSES_ROUTE = f"/openai/{API_VERSION}/responses"
KIND_NOT_KNOWN = (
    "the type of the agent is not known until the Foundry project was asked about it, and only a prompt agent "
    "is asked a follow-up"
)
NO_FOLLOW_UP_USAGE = "the response to the follow-up carried no usage"
NO_FOLLOW_UP_RESPONSE = "the Foundry project returned no response to the follow-up, so no usage was reported"
# The evidence source to declare when `native_tool_calls` records the action evidence.
NATIVE_TOOL_CALLS = "tool calls in the output item of the Foundry cloud evaluation run"
# A run without a testing criterion fails before the agent is invoked. This one is not a judge: it
# compares the identity of the Attempt with itself, and never looks at what the agent did.
CRITERION = {
    "type": "string_check",
    "name": "required_by_the_platform_not_a_judgement",
    "input": "{{item.attempt_id}}",
    "reference": "{{item.attempt_id}}",
    "operation": "eq",
}
OUTPUT_ITEM_PAGE = 100
ENDED = ("completed", "failed", "canceled")
# How much of an answer that is not what was asked for is quoted in an error.
_SHOWN_ANSWER = 2000
NO_RUN = "no cloud evaluation run is known to have been made, so no usage was reported"
RESULT_NOT_READ = "the result of the cloud evaluation run was not read, so no usage is known"
NO_OUTPUT_ITEM = "the cloud evaluation run returned no output item, so no usage was reported"
NO_USAGE = "the output item of the cloud evaluation run carried no usage"
# Where a count comes from, by the type of the agent. The usage of an output item was observed to be the
# usage of the response the run invoked the agent for, under other field names. Foundry runs a prompt agent
# and reports that usage itself. A hosted agent's own code produces its response, usage included.
_OF_THE_ITEM = "the usage of the output item that Foundry cloud evaluation returned for the Attempt"
USAGE_SOURCE = {
    "prompt": f"{_OF_THE_ITEM}: what Foundry reports for the response of the prompt agent",
    "hosted": (
        f"{_OF_THE_ITEM}: what the response of the hosted agent stated. A hosted agent's own code produces its "
        "response, so this is the agent's own count: Foundry passes it on, and it is not checked against what a "
        "model deployment metered"
    ),
}
PLATFORM_DIAGNOSTICS_MEANING = (
    "Foundry cloud evaluation does not invoke the agent without a testing criterion, and labels every item "
    "against it. The criterion compares the identity of the Attempt with itself, so its labels and counts "
    "say nothing about the agent, the Task or the Attempt: they are platform diagnostics, not a task "
    "outcome and not the customer's acceptance criterion."
)
ELAPSED_TIME_BOUNDARY = (
    "the latency of the target that Foundry cloud evaluation reported for the Attempt's output item, measured by "
    "Foundry. Making the run, waiting for it, the testing criterion and reading the result are not included"
)
TOOL_CALL_BOUNDARY = (
    "tool calls in the output item that Foundry cloud evaluation returned for the Attempt, counted by the agent "
    "target. A tool call the platform does not report there is not counted"
)


class Project(Protocol):
    """What the target needs of a Foundry project. `managed_scoring.FoundryProject` is the real one.

    `request` returns the JSON the project answers with, and raises `FoundryRequestError` when
    it is not answered or answered with an error status.
    """

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any: ...


class UnsupportedCombination(InvocationFailed):
    """The agent is not one that has been invoked through cloud evaluation.

    It was not invoked, and nothing was tried instead.
    """


class RunTimedOut(InvocationFailed, TimeoutError):
    """The cloud evaluation run had not ended at the time limit. The sweep records the Attempt as timed out."""


class FoundryCloudEvaluationTarget:
    """Invokes one agent of one Foundry project through cloud evaluation, one evaluation run per Attempt.

    `project` is the customer's Foundry project, a `managed_scoring.FoundryProject`, and
    `agent_name` the name of a prompt agent or of a hosted agent that serves the Responses
    protocol in it. `agent_version` pins the version the run invokes. Without it the project
    chooses, and the version that answered is recorded on each Attempt.

    Every Attempt is a run of its own with one row, so that the sweep keeps repetition,
    grouping, the order of the Attempts and the customer controls around each of them. All
    runs are made in one eval, which the target creates in the project on its first Attempt.
    Foundry starts a new conversation for every run. That resets nothing outside the conversation.

    Foundry does not invoke the agent without a testing criterion. The target attaches
    `CRITERION`, which is no judge and decides nothing: what Foundry labels with it is kept
    as platform diagnostics only.

    `compose_input` gives what the agent is sent for an Attempt instead of its prompt, for an
    agent that has to be told which test data the Attempt works on. Without it the agent is
    sent the Attempt's prompt exactly as it is. A composed input is recorded on the Attempt.
    Under a prompt variation the prompt is the exact text of a prompt variant, so whatever
    `compose_input` adds to it has to be the same under the baseline and every variant.

    `run_timeout_seconds` is how long the target waits for a run to end, from when it is
    invoked for the Attempt, unless a time limit is declared for the Attempt: the target then
    stops at that limit. Either way it asks the project to cancel the run, which does not stop
    an agent that was already invoked. `poll_interval_seconds` is how often it asks the project
    for the run. `clock` gives seconds and never runs backwards, and `sleep` waits for a number
    of them.

    The target asks a prompt agent for its confidence rating in an isolated follow-up, and a hosted
    agent not at all: see `isolated_follow_up` and `elicit_confidence`.
    """

    def __init__(
        self,
        *,
        project: Project,
        agent_name: str,
        agent_version: str | None = None,
        compose_input: Callable[[AttemptRequest], str] | None = None,
        run_timeout_seconds: float = 900.0,
        poll_interval_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not callable(getattr(project, "request", None)):
            raise ValueError(f"project is the Foundry project to make the runs in, a FoundryProject. Got {project!r}")
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise ValueError(f"an agent is identified by its name, got {agent_name!r}")
        if agent_version is not None and (not isinstance(agent_version, str) or not agent_version.strip()):
            raise ValueError(f"the version of an agent is text, such as '3', or left out. Got {agent_version!r}")
        for name, seconds, least in (
            ("run_timeout_seconds", run_timeout_seconds, "positive"),
            ("poll_interval_seconds", poll_interval_seconds, "not negative"),
        ):
            valid = type(seconds) in (int, float) and isfinite(seconds) and seconds >= 0
            if not valid or (least == "positive" and seconds == 0):
                raise ValueError(f"{name} is a finite number of seconds that is {least}, got {seconds!r}")
        self._project = project
        self._agent_name = agent_name
        self._agent_version = agent_version
        self._compose_input = compose_input
        self._run_timeout_seconds = run_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._sleep = sleep
        # What the project said about the agent, without its settings, and the eval the runs are made in, once known.
        self._agent: AgentDetails | None = None
        self._eval_id: str | None = None
        # The Attempts of Tasks given without an experiment may be invoked from several threads.
        self._lock = threading.Lock()

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        # Without a composed input the agent is sent the Attempt's prompt exactly as it is.
        composed = None if self._compose_input is None else self._compose_input(request)
        recorded = _Recorded(composed_input=composed)
        # The time limit the customer declared for the Attempt comes before the target's own.
        limit = self._run_timeout_seconds if request.timeout_seconds is None else request.timeout_seconds
        deadline = self._clock() + limit
        recorded.agent_kind = self._supported_kind(recorded)
        with _asking(recorded, "the eval for the cloud evaluation runs could not be made in the Foundry project"):
            recorded.eval_id = self._eval(recorded)
        runs = f"/openai/{API_VERSION}/evals/{recorded.eval_id}/runs"
        with _asking(recorded, "the cloud evaluation run could not be made in the Foundry project"):
            made = self._project.request(
                "POST", runs, self._run_of(request, request.prompt if composed is None else composed)
            )
        if not isinstance(made, dict) or not made.get("id"):
            raise InvocationFailed(
                "the Foundry project answered the making of the cloud evaluation run with something that is no "
                f"run: {json.dumps(made)[:_SHOWN_ANSWER]}",
                invocation=recorded.invocation(),
            )
        recorded.run = made
        run_path = f"{runs}/{made['id']}"
        self._wait(recorded, run_path, deadline, limit)
        with _asking(recorded, f"the output items of the cloud evaluation run {made['id']} could not be read"):
            items = self._output_items(run_path)
        recorded.result_read = True
        # Paired by the identity of the Attempt, never by the order of the rows.
        own = [item for item in items if (item.get("datasource_item") or {}).get("attempt_id") == request.attempt_id]
        if len(items) == 1 and own:
            (recorded.item,) = own
        invocation = recorded.invocation()
        failure = _failure_of(recorded.run, items, recorded.item, request.attempt_id)
        if failure is not None:
            raise InvocationFailed(failure, invocation=invocation)
        return AgentResponse(
            output=recorded.item["datasource_item"].get("sample.output_text") or "",
            resource_measurements=_resource_measurements(invocation),
            invocation=invocation,
        )

    @property
    def isolated_follow_up(self) -> IsolatedFollowUp:
        """Whether the agent can be asked a follow-up in isolation: a prompt agent can, a hosted agent cannot.

        The type of the agent is what the project said when the target looked the agent up, on
        its first Attempt or for `agent_configuration`. Until then nothing is declared supported.
        """
        kind = None if self._agent is None else self._agent.kind
        if kind == "prompt":
            return IsolatedFollowUp(supported=True)
        if kind == "hosted":
            # For the reason the hosted agent target gives: the entry point does not change whose code decides.
            return FoundryHostedAgentTarget.isolated_follow_up
        return IsolatedFollowUp(supported=False, reason=KIND_NOT_KNOWN)

    def elicit_confidence(self, request: ConfidenceRequest) -> ConfidenceResponse:
        """Put the confidence question to a prompt agent in the conversation of one completed Attempt.

        The Attempt was invoked by Foundry cloud evaluation, and its output item names the response
        of the agent. The follow-up is a request of this process to the project's Responses route,
        made as the prompt agent target makes it: it names that response as the one it continues,
        the agent and version that answered, and the tool choice `none`, under which the project
        runs the agent without letting it call a tool. An answer the project does not say that
        about, or one given after a tool call, is not returned: the request then fails.
        """
        attempt = request.response.invocation or {}
        if attempt.get("agent_kind") != "prompt":
            raise InvocationFailed(
                f"only a prompt agent is asked a follow-up, and the Attempt records an agent of kind "
                f"{attempt.get('agent_kind')!r}"
            )
        previous = attempt.get("response_id")
        answered_by = attempt.get("agent") or {}
        agent = {"type": "agent_reference", "name": answered_by.get("name") or self._agent_name}
        if answered_by.get("version"):
            # The version that answered the Attempt answers the question about it.
            agent["version"] = answered_by["version"]
        body = {
            "input": request.prompt,
            "previous_response_id": previous,
            "agent_reference": agent,
            "tool_choice": TOOL_CHOICE_NONE,
            "metadata": {
                "attempt_id": request.attempt.attempt_id,
                "task_id": request.attempt.task_id,
                "request": "confidence",
            },
        }
        try:
            response = self._project.request("POST", RESPONSES_ROUTE, body)
        except FoundryRequestError as error:
            raise InvocationFailed(
                f"the follow-up could not be made in the Foundry project: {error}",
                invocation=_follow_up({}, request.prompt, error=str(error)),
            ) from error
        if not isinstance(response, dict) or output_items(response) is None:
            raise InvocationFailed(
                "the Foundry project answered the follow-up with something that is no response: "
                f"{json.dumps(response)[:_SHOWN_ANSWER]}",
                invocation=_follow_up({}, request.prompt),
            )
        follow_up = _follow_up(response, request.prompt)
        failure = failure_of_a_response(response) or not_isolated(follow_up, previous)
        if failure is not None:
            raise InvocationFailed(failure, invocation=follow_up)
        return ConfidenceResponse(
            attempt_id=request.attempt.attempt_id, output=output_text(response), invocation=follow_up
        )

    def agent_configuration(self) -> dict[str, Any]:
        """What identifies the agent, as the project reports it, to declare as an experiment's agent configuration.

        Raises UnsupportedCombination for an agent this target does not invoke, before any
        Attempt is made. The agent's settings, which the project returns with its details,
        are not copied: they can hold secrets.
        """
        kind = self._supported_kind(_Recorded(composed_input=None))
        return {
            "agent_target": AGENT_TARGET,
            "entry_point": FOUNDRY_CLOUD_EVALUATION,
            "project_endpoint": getattr(self._project, "endpoint", None),
            "agent": self._agent_name,
            "kind": kind,
            "protocols": self._agent.protocols,
            "pinned_version": self._agent_version,
            "latest_version": self._agent.latest_version,
        }

    def _supported_kind(self, recorded: _Recorded) -> str:
        """The type of the agent, when it is one that has been invoked through cloud evaluation.

        Raises otherwise. The agent is looked up once. An agent that is not supported is never invoked.
        The project answers with a hosted agent's settings as well, in plain text. They are dropped
        where the answer is read, so no error and no record of the target holds them.
        """
        with self._lock:
            if self._agent is None:
                with _asking(recorded, f"agent {self._agent_name!r} could not be looked up in the Foundry project"):
                    self._agent = read_agent_details(
                        self._project.request("GET", f"/agents/{self._agent_name}?api-version={API_VERSION}")
                    )
        kind = self._agent.kind
        recorded.agent_kind = kind
        protocols = self._agent.protocols or []
        if kind == "hosted" and RESPONSES_PROTOCOL not in protocols:
            raise UnsupportedCombination(
                f"hosted agent {self._agent_name!r} serves the protocols {protocols} and not the Responses protocol. "
                "Invoking such an agent through Foundry cloud evaluation has not been verified, so it was not invoked",
                invocation=recorded.invocation(),
            )
        if kind not in ("prompt", "hosted"):
            raise UnsupportedCombination(
                f"agent {self._agent_name!r} is of kind {kind!r}. Only prompt agents, and hosted agents that serve "
                "the Responses protocol, have been invoked through Foundry cloud evaluation, so it was not invoked",
                invocation=recorded.invocation(),
            )
        return kind

    def _eval(self, recorded: _Recorded) -> str:
        """The eval every run of this target is made in. It is created once."""
        with self._lock:
            if self._eval_id is None:
                created = self._project.request(
                    "POST",
                    f"/openai/{API_VERSION}/evals",
                    {
                        "name": f"Reliability Evaluators: Attempts of {self._agent_name} through cloud evaluation",
                        "data_source_config": {
                            "type": "custom",
                            "item_schema": {
                                "type": "object",
                                "properties": {
                                    field: {"type": "string"} for field in ("query", "attempt_id", "task_id")
                                },
                            },
                            "include_sample_schema": True,
                        },
                        "testing_criteria": [CRITERION],
                    },
                )
                if not isinstance(created, dict) or not created.get("id"):
                    raise InvocationFailed(
                        "the Foundry project answered the making of the eval for the cloud evaluation runs with "
                        f"something that is no eval: {json.dumps(created)[:_SHOWN_ANSWER]}",
                        invocation=recorded.invocation(),
                    )
                self._eval_id = created["id"]
        return self._eval_id

    def _run_of(self, request: AttemptRequest, sent: str) -> dict[str, Any]:
        """The run that invokes the agent for one Attempt."""
        target = {"type": "azure_ai_agent", "name": self._agent_name}
        if self._agent_version is not None:
            target["version"] = self._agent_version
        return {
            "name": f"Attempt {request.attempt_id}"[:128],
            "data_source": {
                "type": "azure_ai_target_completions",
                # One row: the run invokes the agent for this Attempt and for nothing else. The identity
                # of the Attempt travels with the row, and the output item returns it.
                "source": {
                    "type": "file_content",
                    "content": [
                        {"item": {"query": sent, "attempt_id": request.attempt_id, "task_id": request.task_id}}
                    ],
                },
                "input_messages": {
                    "type": "template",
                    "template": [
                        {"type": "message", "role": "user", "content": {"type": "input_text", "text": "{{item.query}}"}}
                    ],
                },
                "target": target,
            },
        }

    def _wait(self, recorded: _Recorded, run_path: str, deadline: float, limit: float) -> None:
        """Wait until the run has ended. At the time limit its cancellation is requested and the Attempt timed out."""
        run_id = recorded.run.get("id")
        while recorded.run.get("status") not in ENDED:
            remaining = deadline - self._clock()
            if remaining <= 0:
                status = recorded.run.get("status")
                try:
                    cancelled = self._project.request("POST", f"{run_path}/cancel")
                    if isinstance(cancelled, dict):
                        recorded.run = cancelled
                except FoundryRequestError:
                    # The timeout is what the caller is told. A run that could not be cancelled ends by itself.
                    pass
                raise RunTimedOut(
                    f"the cloud evaluation run {run_id} had not ended within {limit:g} seconds, in status "
                    f"{status!r}. Its cancellation was requested, which does not stop an agent that was already "
                    "invoked",
                    invocation=recorded.invocation(),
                )
            self._sleep(min(self._poll_interval_seconds, remaining))
            with _asking(recorded, f"the cloud evaluation run {run_id} could not be read"):
                answered = self._project.request("GET", run_path)
            # An answer that is no run says nothing about the run, so the run is asked for again.
            if isinstance(answered, dict):
                recorded.run = answered

    def _output_items(self, run_path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        after = ""
        while True:
            page = self._project.request("GET", f"{run_path}/output_items?limit={OUTPUT_ITEM_PAGE}{after}")
            page = page if isinstance(page, dict) else {}
            items.extend(item for item in page.get("data") or [] if isinstance(item, dict))
            if not page.get("has_more") or not page.get("last_id"):
                return items
            after = f"&after={page['last_id']}"


@dataclass
class _Recorded:
    """What is known of the invocation of one Attempt, as far as it got."""

    composed_input: str | None
    agent_kind: str | None = None
    eval_id: str | None = None
    # The run as the project last returned it, whether its output items were read, and the item of the Attempt.
    run: dict[str, Any] | None = None
    result_read: bool = False
    item: dict[str, Any] | None = None

    def invocation(self) -> dict[str, Any]:
        """What the platform returned about the Attempt, as it returned it. What was not reached is None."""
        run, item = self.run or {}, self.item
        returned = (item or {}).get("datasource_item") or {}
        sample = (item or {}).get("sample") or {}
        usage = sample.get("usage")
        return {
            "agent_target": AGENT_TARGET,
            "entry_point": FOUNDRY_CLOUD_EVALUATION,
            "api_version": API_VERSION,
            "agent_kind": self.agent_kind,
            "composed_input": self.composed_input,
            "eval_id": self.eval_id,
            "run_id": run.get("id"),
            "run_status": run.get("status"),
            "output_item_id": None if item is None else item.get("id"),
            "status": None if item is None else item.get("status"),
            "criterion": CRITERION,
            "agent": None
            if item is None
            else {
                "id": returned.get("agent_id"),
                "name": returned.get("agent_name"),
                "version": returned.get("agent_version"),
            },
            "response_id": returned.get("response_id"),
            "conversation_id": returned.get("conversation_id"),
            "model": sample.get("model"),
            "metadata": None
            if item is None
            else {"attempt_id": returned.get("attempt_id"), "task_id": returned.get("task_id")},
            # The error of the item's sample. Without an item, the error of the run.
            "error": sample.get("error") if item is not None else _error_of(run),
            # As the platform reported it for the item. Usage that was not reported is missing, never zero.
            "token_usage": usage,
            "token_usage_missing": self._usage_missing(usage),
            "token_usage_source": None if usage is None else USAGE_SOURCE.get(self.agent_kind, _OF_THE_ITEM),
            # The run's own rows. They can count the same tokens more than once, so they are not a total.
            "run_per_model_usage": run.get("per_model_usage"),
            "latency_ms": sample.get("latency_ms"),
            "tool_calls": None if item is None else _tool_calls(returned),
            # The run's link to the Foundry portal is left out: it embeds the customer's subscription identifier.
            "platform_diagnostics": {
                "meaning": PLATFORM_DIAGNOSTICS_MEANING,
                "result_counts": run.get("result_counts"),
                "per_testing_criteria_results": run.get("per_testing_criteria_results"),
                "results": None if item is None else item.get("results"),
            },
        }

    def _usage_missing(self, usage: Any) -> str | None:
        """Why there is no usage for the Attempt, when there is none."""
        if usage is not None:
            return None
        if self.item is not None:
            return NO_USAGE
        if self.run is None:
            return NO_RUN
        return NO_OUTPUT_ITEM if self.result_read else RESULT_NOT_READ


@contextlib.contextmanager
def _asking(recorded: _Recorded, failure: str) -> Iterator[None]:
    """A request to the project that fails is a failed invocation, with what was recorded until then."""
    try:
        yield
    except FoundryRequestError as error:
        raise InvocationFailed(f"{failure}: {error}", invocation=recorded.invocation()) from error


def _follow_up(response: dict[str, Any], question: str, error: str | None = None) -> dict[str, Any]:
    """What the project returned about one follow-up, in the form of an invocation.

    `response` is the response object the project answered with, or empty when it answered with
    none. `question` is what the agent was sent. With it are the response the project says the
    follow-up continued and the tool choice it says it applied.
    """
    usage = response.get("usage")
    return {
        "agent_target": AGENT_TARGET,
        # The follow-up is sent by this process. Only the Attempt it follows was invoked by cloud evaluation.
        "entry_point": CUSTOMER_CODE,
        "protocol": RESPONSES_PROTOCOL,
        "api_version": API_VERSION,
        "agent": response.get("agent_reference"),
        "response_id": response.get("id"),
        "status": response.get("status"),
        "model": response.get("model"),
        "metadata": response.get("metadata"),
        "error": response.get("error") if response else error,
        # As the platform reported it. Usage that was not reported is missing, never zero.
        "token_usage": usage,
        "token_usage_missing": None if usage is not None else NO_FOLLOW_UP_USAGE if response else NO_FOLLOW_UP_RESPONSE,
        "token_usage_source": None if usage is None else USAGE_SOURCE_OF_A_RESPONSE,
        "tool_calls": tool_calls_of_a_response(response),
        "input": question,
        "previous_response_id": response.get("previous_response_id"),
        "tool_choice": response.get("tool_choice"),
    }


def _failure_of(
    run: dict[str, Any], items: list[dict[str, Any]], item: dict[str, Any] | None, attempt_id: str
) -> str | None:
    """Why a run that ended is not a completed execution of the agent for the Attempt, or None when it is one.

    The status of the run and of the item do not prove it: both were observed to be completed,
    with the criterion passed, on an item whose target failed. The item's sample error does.
    """
    this = f"the cloud evaluation run {run.get('id')}"
    if run.get("status") != "completed":
        without_item = "" if items else " and returned no output item"
        return f"{this} ended as {run.get('status')!r}{without_item}: {_described(_error_of(run))}"
    if len(items) != 1:
        return f"{this} returned {len(items)} output items for the one Attempt it was given"
    if item is None:
        other = (items[0].get("datasource_item") or {}).get("attempt_id")
        return f"{this} returned an output item for {other!r} and not for Attempt {attempt_id!r}"
    error = (item.get("sample") or {}).get("error")
    if error:
        return (
            f"{this} and its output item report {item.get('status')!r}, and the item carries a target error: "
            f"{_described(error)}"
        )
    if item.get("status") != "completed":
        return f"the output item of {this} ended with status {item.get('status')!r}"
    if not (item.get("datasource_item") or {}).get("response_id"):
        return (
            f"the output item of {this} reports {item.get('status')!r} and names no response of the agent, "
            "so nothing shows that the agent ran"
        )
    return None


def _error_of(run: dict[str, Any]) -> Any:
    """The error of a run, or None when it carries none. A run without an error can have one with empty fields."""
    error = run.get("error")
    if isinstance(error, dict) and not any(value for value in error.values()):
        return None
    return error


def _described(error: Any) -> str:
    """An error as it is quoted in the reason of a failure: its code, message and details, or the error as it is."""
    if isinstance(error, dict) and ("code" in error or "message" in error):
        details = "; ".join(
            f"{detail.get('code')}: {detail.get('message')}"
            for detail in error.get("details") or []
            if isinstance(detail, dict)
        )
        return f"{error.get('code')}: {error.get('message')}" + (f" ({details})" if details else "")
    return error if isinstance(error, str) else json.dumps(error)


def _resource_measurements(invocation: dict[str, Any]) -> tuple[ResourceMeasurement, ...]:
    """What the platform measured of one Attempt. A measurement it did not report is left out."""
    measured = []
    latency_ms = invocation["latency_ms"]
    if type(latency_ms) in (int, float) and isfinite(latency_ms) and latency_ms >= 0:
        # In the platform's own unit, so that the number is the one it reported.
        measured.append(
            ResourceMeasurement(
                resource_type=ResourceType.ELAPSED_TIME,
                value=latency_ms,
                unit="milliseconds",
                boundary=ELAPSED_TIME_BOUNDARY,
            )
        )
    if invocation["tool_calls"] is not None:
        measured.append(
            ResourceMeasurement(
                resource_type=ResourceType.TOOL_CALLS,
                value=len(invocation["tool_calls"]),
                unit="calls",
                boundary=TOOL_CALL_BOUNDARY,
            )
        )
    return tuple(measured)


def native_tool_calls(request: AttemptRequest, response: AgentResponse) -> list[ActionEvent] | None:
    """The tool calls the output item of an Attempt exposed, in order, as action evidence.

    Declare it as the `record` of an ActionEvidenceDeclaration, with NATIVE_TOOL_CALLS as
    the evidence source. The platform says what each tool returned and not whether the
    call failed, so no event carries an error: a failure shows only in the result.
    Returns None when the output item exposed no tool calls to read.
    """
    recorded = response.invocation or {}
    if recorded.get("agent_target") != AGENT_TARGET or recorded.get("tool_calls") is None:
        return None
    return [
        ActionEvent(tool_name=call["name"], arguments=call["arguments"], result=call["output"])
        for call in recorded["tool_calls"]
    ]


def _tool_calls(returned: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The tool calls the output item exposes, in its order, each with what the tool returned.

    The item lists them as messages: the assistant's hold the calls, whose type ends in `_call`,
    and the tool's hold what a call returned, under a key named after its type, such as
    `openapi_call_output`. The two are paired by `tool_call_id`.

    None when the item has no list of them: the calls were then not exposed, which is not the same as no call.
    """
    messages = returned.get("sample.tool_calls")
    if not isinstance(messages, list):
        return None
    parts = [
        (message, part)
        for message in messages
        if isinstance(message, dict) and isinstance(message.get("content"), list)
        for part in message["content"]
        if isinstance(part, dict)
    ]
    outputs = {
        message.get("tool_call_id"): part.get(part["type"])
        for message, part in parts
        if str(part.get("type", "")).endswith("_call_output")
    }
    return [
        {
            "call_id": part.get("tool_call_id"),
            "type": part["type"],
            # A tool type that gives its calls no name is named after its type.
            "name": part.get("name") or part["type"],
            "arguments": part.get("arguments"),
            "output": outputs.get(part.get("tool_call_id")),
        }
        for _, part in parts
        if str(part.get("type", "")).endswith("_call")
    ]
