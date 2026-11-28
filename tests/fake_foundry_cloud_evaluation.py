"""A fake Foundry project for driving Foundry cloud evaluation with an agent target. No Azure access.

It answers, in this process, the project REST requests that the cloud evaluation agent target
makes, in the shapes observed on a live project on 2026-11-27 (see
docs/foundry_cloud_evaluation/readme.md, "Observed live"):

- `GET /agents/{name}?api-version=v1`: what the project says about the agent.
- An eval is created whatever its testing criteria. A run on an eval without one fails with
  `ArgumentInvalid`, and the agent is not invoked.
- A run is queued, then in progress, then completed, failed or canceled. A run whose agent or
  agent version the project does not have fails with `EvalValidationFailed`.
- A run invokes the agent once for every row it is given, and returns one output item per row.
- An item returns the fields of its row, the response, conversation and agent identifiers, the
  agent's text and its tool calls. Tool-call arguments are objects.
- A `string_check` criterion that compares two fields of the row passes whatever the agent did.
- A prompt agent whose tool returns an HTTP error fails as a target: the run and the item are
  `completed`, the criterion passes, and only `sample.error` says `TARGET_INVOCATION_FAILED`.
  The item then has no response identifier, no usage and no latency.
- A hosted agent whose tool returns an HTTP error answers all the same, and its item carries no error.
- A cancelled run has no output items.
- `POST /openai/v1/responses` with `previous_response_id` is a follow-up in the conversation of the
  response an output item named, for a prompt agent (observed on 2026-11-27 for the full-matrix
  verification, see docs/coverage_matrix/readme.md). With the tool choice `none` the agent calls no
  tool. The response of a hosted agent is not one this route knows.
- The run's `report_url` embeds an encoded subscription identifier.

The agents are scripted and work on a to-do service through the tools of the live fixture's
agents, with the agents' key. So only the service knows what an agent really did.

A test scripts what a live project could do besides: never finish a run, refuse requests,
label the criterion as failed, or return another item than the one the row asked for.
"""

from __future__ import annotations

import copy
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from reliability_evaluators.managed_scoring import FoundryRequestError

ENDPOINT = "https://fake.services.ai.azure.com/api/projects/fake"
MODEL_DEPLOYMENT = "todo-gpt-41-mini"
INSTRUCTIONS = "Use the to-do tools for all to-do operations."
ANSWER = "I have completed all the requested operations."
TOOL_USER_ERROR = (
    "(UserError) Error code: 400 - {'error': {'message': 'A user error occurred while executing a tool call.', "
    "'type': 'invalid_request_error', 'param': None, 'code': 'tool_user_error', 'request_id': 'err_fake'}}"
)

# Each step is one tool call: the operation, the HTTP request the tool makes, the item, and the arguments.
READ_TARGET = ("read", "GET", "target", {})
CREATE_SCRATCH = ("create", "POST", None, {"id": "scratch", "title": "Temporary"})
READ_SCRATCH = ("read", "GET", "scratch", {})
DELETE_SCRATCH = ("delete", "DELETE", "scratch", {})
UPDATE_TARGET = ("update", "PATCH", "target", {"done": True})
THE_TASK = (READ_TARGET, CREATE_SCRATCH, READ_SCRATCH, DELETE_SCRATCH, UPDATE_TARGET)
BEHAVIOURS = {
    "completes": THE_TASK,
    # It still answers that it completed everything.
    "skips the update": THE_TASK[:4],
    "updates before it deletes": (READ_TARGET, CREATE_SCRATCH, READ_SCRATCH, UPDATE_TARGET, DELETE_SCRATCH),
    "does nothing": (),
}
TOOL_NAMES = {
    "prompt": {
        "read": "todos_readTodo",
        "create": "todos_createTodo",
        "delete": "todos_deleteTodo",
        "update": "todos_updateTodo",
    },
    "hosted": {"read": "read_todo", "create": "create_todo", "delete": "delete_todo", "update": "update_todo"},
}
CALL_TYPES = {"prompt": "openapi_call", "hosted": "function_call"}
USAGE_FIELDS = ("total_tokens", "prompt_tokens", "completion_tokens", "cached_tokens")


@dataclass
class FakeAgent:
    """An agent of the fake project: what the project says about it, and what it does when invoked.

    `script` gives what the agent does on each invocation, in order: a behaviour in BEHAVIOURS.
    An invocation with nothing scripted completes the Task.

    `confidence` gives what the agent answers to each follow-up, in order. A follow-up with
    nothing scripted is answered "100". `acts_on_follow_up` makes the agent read the item
    `unrelated` in every follow-up in which the project lets it call a tool.
    """

    kind: str = "prompt"
    protocols: tuple[str, ...] = ("responses",)
    versions: tuple[str, ...] = ("1",)
    script: list[str] = field(default_factory=list)
    confidence: list[str] = field(default_factory=list)
    acts_on_follow_up: bool = False

    def details(self, name: str) -> dict:
        latest = self.versions[-1]
        definition: dict[str, Any] = {"kind": self.kind}
        if self.kind == "hosted":
            # The live project returned the hosted agent's settings with its details, secrets included.
            definition["environment_variables"] = {"SYNTHETIC_SERVICE_KEY": "synthetic-secret"}
        return {
            "object": "agent",
            "id": name,
            "name": name,
            "versions": {
                "latest": {
                    "object": "agent.version",
                    "id": f"{name}:{latest}",
                    "name": name,
                    "version": latest,
                    "status": "active",
                    "definition": definition,
                }
            },
            "agent_endpoint": {
                "version_selector": {
                    "version_selection_rules": [
                        {"type": "FixedRatio", "agent_version": "@latest", "traffic_percentage": 100}
                    ]
                },
                "protocols": list(self.protocols),
                "protocol_configuration": {protocol: {} for protocol in self.protocols},
            },
        }


class FakeCloudEvaluationProject:
    """The fake project. `request` is what a `FoundryProject` offers.

    `agents` are its agents by name, working on `todo_service`.

    - `polls_until_finished`: how often a run is reported as in progress before it has ended.
    - `never_finishes`: every run stays in progress until it is cancelled.
    - `refuses`: the error every request fails with. `refuses_runs`: the error making a run fails with.
    - `criterion_fails`: the criterion's result is labelled as failed, whatever the agent did.
    - `alter_item`: given each output item, returns the item the project returns instead, or None for none.
    - `follow_up`: how the project treats a follow-up. "as observed" is the live project's behaviour. The
      others were never observed and show what the target does about them: "ignores tool_choice" runs the
      follow-up as if no tool choice had been sent, "starts a new conversation" answers without the earlier
      response, and "refuses" answers HTTP 400.
    """

    endpoint = ENDPOINT

    def __init__(
        self,
        todo_service: Any,
        agents: dict[str, FakeAgent],
        *,
        polls_until_finished: int = 1,
        never_finishes: bool = False,
        refuses: FoundryRequestError | None = None,
        refuses_runs: FoundryRequestError | None = None,
        criterion_fails: bool = False,
        alter_item: Callable[[dict], dict | None] | None = None,
        follow_up: str = "as observed",
    ):
        self._todo_service = todo_service
        self.agents = agents
        self._polls_until_finished = polls_until_finished
        self._never_finishes = never_finishes
        self._refuses = refuses
        self._refuses_runs = refuses_runs
        self._criterion_fails = criterion_fails
        self._alter_item = alter_item
        self._follow_up_as = follow_up
        # The state of the to-do service each response of a prompt agent worked on, and the agent, by response.
        self._conversations: dict[str, tuple[str, str]] = {}
        # Every follow-up request the project was sent, as it was sent.
        self.follow_ups: list[dict] = []
        self.evals: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self._output_items: dict[str, list[dict]] = {}
        self._polls: dict[str, int] = {}
        self._responses = 0

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any:
        if self._refuses is not None:
            raise self._refuses
        routes = (
            ("GET", r"/agents/([^/?]+)\?api-version=v1", self._agent),
            ("POST", r"/openai/v1/evals", self._create_eval),
            ("POST", r"/openai/v1/evals/([^/]+)/runs", self._create_run),
            ("GET", r"/openai/v1/evals/([^/]+)/runs/([^/]+)", self._poll_run),
            ("POST", r"/openai/v1/evals/([^/]+)/runs/([^/]+)/cancel", self._cancel_run),
            ("GET", r"/openai/v1/evals/([^/]+)/runs/([^/]+)/output_items\?limit=(\d+)(?:&after=(\w+))?", self._items),
            ("POST", r"/openai/v1/responses", self._follow_up),
        )
        for route_method, pattern, handler in routes:
            matched = re.fullmatch(pattern, path)
            if method == route_method and matched:
                return copy.deepcopy(handler(*matched.groups(), body=body))
        raise FoundryRequestError(method, path, 404, '{"error": {"code": "not_found", "message": "unknown route"}}')

    def _agent(self, name, **_):
        if name not in self.agents:
            error = {"code": "not_found", "message": f"Agent {name} was not found."}
            raise FoundryRequestError("GET", f"/agents/{name}", 404, json.dumps({"error": error}))
        return self.agents[name].details(name)

    def _create_eval(self, *, body, **_):
        eval_id = f"eval_fake_{len(self.evals) + 1}"
        self.evals[eval_id] = {
            "object": "eval",
            "id": eval_id,
            "name": body["name"],
            "data_source_config": body["data_source_config"],
            "testing_criteria": body["testing_criteria"],
        }
        return self.evals[eval_id]

    def _create_run(self, eval_id, *, body, **_):
        if self._refuses_runs is not None:
            raise self._refuses_runs
        run_id = f"evalrun_fake_{len(self.runs) + 1}"
        source = body["data_source"]
        target = source["target"]
        rows = [row["item"] for row in source["source"]["content"]]
        error, items = self._execute(eval_id, run_id, target, source["input_messages"], rows)
        # Observed live: an item whose target failed is counted as passed and not as errored.
        errored = 0
        criteria = self.evals[eval_id]["testing_criteria"]
        passed = 0 if self._criterion_fails else len(items)
        failed = len(items) - passed
        run = {
            "object": "eval.run",
            "id": run_id,
            "eval_id": eval_id,
            "name": body["name"],
            "status": "failed" if error else "completed",
            "data_source": {
                **source,
                "target": {**target, "version": target.get("version"), "tool_descriptions": []},
                "item_generation_params": {"type": "target_completions", "max_concurrency": 1},
            },
            "report_url": f"https://ai.azure.com/nextgen/r/encoded-subscription-identifier/{eval_id}/run/{run_id}",
            "result_counts": {"total": 0, "passed": 0, "failed": 0}
            if error
            else {"total": len(items), "passed": passed, "failed": failed, "errored": errored, "skipped": 0},
            "per_model_usage": [] if error else self._per_model_usage(target["name"], items),
            "per_testing_criteria_results": []
            if error
            else [
                {"testing_criteria": criterion["name"], "passed": passed, "failed": failed, "errored": 0, "skipped": 0}
                for criterion in criteria
            ],
            "error": error,
        }
        self.runs[run_id] = run
        self._output_items[run_id] = items
        self._polls[run_id] = 0
        return {
            **run,
            "status": "queued",
            "report_url": None,
            "result_counts": {"total": 0, "passed": 0, "failed": 0},
            "per_model_usage": [],
            "per_testing_criteria_results": [],
            "error": {"code": None, "message": None, "correlation_id": None, "inner_error": None},
        }

    def _poll_run(self, eval_id, run_id, **_):
        self._polls[run_id] += 1
        run = self.runs[run_id]
        if run["status"] == "canceled":
            return run
        if self._never_finishes or self._polls[run_id] <= self._polls_until_finished:
            unfinished = {"error": None, "per_model_usage": [], "per_testing_criteria_results": []}
            return {**run, "status": "in_progress", **unfinished}
        return run

    def _cancel_run(self, eval_id, run_id, **_):
        self.runs[run_id] = {
            **self.runs[run_id],
            "status": "canceled",
            "error": None,
            "result_counts": {"total": 0, "passed": 0, "failed": 0},
            "per_model_usage": [],
            "per_testing_criteria_results": [],
        }
        return self.runs[run_id]

    def _items(self, eval_id, run_id, limit, after, **_):
        items = self._output_items[run_id] if self.runs[run_id]["status"] == "completed" else []
        start = 0 if after is None else next(n for n, item in enumerate(items, start=1) if item["id"] == after)
        page = items[start : start + int(limit)]
        return {
            "data": page,
            "first_id": page[0]["id"] if page else "",
            "last_id": page[-1]["id"] if page else "",
            "has_more": start + len(page) < len(items),
        }

    def _execute(self, eval_id, run_id, target, input_messages, rows) -> tuple[dict | None, list[dict]]:
        """Validate the run and invoke the agent once for every row. A run that fails leaves no items."""
        criteria = self.evals[eval_id]["testing_criteria"]
        if not criteria:
            message = "The evaluators field is a required input in the evaluation request body."
            return {"code": "ArgumentInvalid", "message": message, "correlation_id": None, "inner_error": None}, []
        agent = self.agents.get(target["name"])
        if agent is None or target.get("version") not in (None, *agent.versions):
            return {
                "code": "EvalValidationFailed",
                "message": "Evaluation validation failed: agent resource is not found.",
                "correlation_id": None,
                "target": eval_id,
                "details_uri": "https://aka.ms/FoundryEvalsFormatIssues",
                "details": [
                    {
                        "code": "ResourceNotFound",
                        "target": "run.data_source.target",
                        "message": f"The AzureAIAgentTarget '{target['name']}' was not found. "
                        "Verify the name and version are correct.",
                    }
                ],
                "inner_error": None,
            }, []
        (message,) = input_messages["template"]
        items = []
        for position, row in enumerate(rows):
            sent = re.sub(r"\{\{item\.(\w+)\}\}", lambda field: str(row[field.group(1)]), message["content"]["text"])
            version = target.get("version") or agent.versions[-1]
            item = {
                "object": "eval.run.output_item",
                "id": str(position + 1),
                "run_id": run_id,
                "eval_id": eval_id,
                "status": "completed",
                "datasource_item_id": position,
                **self._invoke(target["name"], agent, version, row, sent),
                "results": [self._result(criterion) for criterion in criteria],
            }
            if self._alter_item is not None:
                item = self._alter_item(item)
            if item is not None:
                items.append(item)
        return None, items

    def _invoke(self, name: str, agent: FakeAgent, version: str, row: dict, sent: str) -> dict:
        """What one invocation of the agent leaves in its output item: the returned row and the sample."""
        self._responses += 1
        number = self._responses
        behaviour = agent.script.pop(0) if agent.script else "completes"
        named = re.search(r"Use attempt_id (\S+)\.", sent)
        state = named.group(1) if named else "unknown"
        tool_calls = []
        for position, (operation, method, todo_id, arguments) in enumerate(BEHAVIOURS[behaviour], start=1):
            status, body = self._call_tool(state, method, todo_id, arguments)
            if status >= 400 and agent.kind == "prompt":
                # Observed live: a tool call of a prompt agent that fails fails the target, and nothing else
                # is returned.
                return self._failed_target(name, row, TOOL_USER_ERROR)
            call_id = f"call_fake_{number}_{position}"
            call_type, tool = CALL_TYPES[agent.kind], TOOL_NAMES[agent.kind][operation]
            passed = {**arguments, "attempt_id": state, **({"todo_id": todo_id} if todo_id else {})}
            # A prompt agent's tool output is text. A hosted agent's is the object its tool returned as JSON.
            returned = {"status": status, "body": body}
            if agent.kind == "prompt":
                returned = json.dumps({"response": json.dumps(body)})
            tool_calls.append(
                {
                    "run_id": "",
                    "role": "assistant",
                    "content": [{"type": call_type, "tool_call_id": call_id, "name": tool, "arguments": passed}],
                }
            )
            tool_calls.append(
                {
                    "run_id": "",
                    "tool_call_id": call_id,
                    "role": "tool",
                    "content": [{"type": f"{call_type}_output", f"{call_type}_output": returned}],
                }
            )
        calls = len(tool_calls) // 2
        text = {"annotations": [], "text": ANSWER, "type": "output_text", "logprobs": []}
        answer = {"role": "assistant", "content": [text]}
        system = [{"role": "system", "content": INSTRUCTIONS}] if agent.kind == "prompt" else []
        prefix = "resp" if agent.kind == "prompt" else "caresp"
        if agent.kind == "prompt":
            self._conversations[f"resp_fake_{number}"] = (state, name)
        return {
            "datasource_item": {
                **row,
                "sample.output_text": ANSWER,
                "response_id": f"{prefix}_fake_{number}",
                "agent_id": f"{name}:{version}",
                "agent_name": name,
                "agent_version": version,
                "conversation_id": f"conv_fake_{number}",
                "previous_response_id": None,
                "sample.tool_calls": tool_calls,
                "sample.tool_definitions": [],
                "sample.output_items": [*system, *tool_calls, answer],
                "trace_id": "",
                "span_id": "",
            },
            "sample": {
                "error": None,
                "finish_reason": "",
                "input": [{"role": "user", "content": json.dumps([{"type": "input_text", "text": sent}])}],
                "max_completion_tokens": None,
                "model": MODEL_DEPLOYMENT if agent.kind == "prompt" else "",
                "output": [],
                "seed": None,
                "temperature": 1.0 if agent.kind == "prompt" else None,
                "top_p": 1.0 if agent.kind == "prompt" else None,
                "usage": {
                    "cached_tokens": 0,
                    "completion_tokens": 20 + 10 * calls,
                    "prompt_tokens": 100 + 100 * calls,
                    "total_tokens": 120 + 110 * calls,
                },
                "latency_ms": 1000.0 + 1000.0 * calls,
            },
        }

    def _follow_up(self, *, body, **_):
        """Answer a request in the conversation of an earlier response, as the live project was observed to."""
        self.follow_ups.append(copy.deepcopy(body))
        previous = body.get("previous_response_id")
        refused = self._follow_up_as == "refuses"
        if refused or previous not in self._conversations:
            message = (
                "ToolChoice must match the agent's tool_choice when agent specifies tool_choice."
                if refused
                else f"Invalid previous_response_id '{previous}'. The Agents Responses API requires an ID returned "
                "by a previous Responses operation."
            )
            error = {"code": "invalid_payload", "message": message, "type": "invalid_request_error"}
            raise FoundryRequestError("POST", "/openai/v1/responses", 400, json.dumps({"error": error}))
        state, name = self._conversations[previous]
        agent = self.agents[name]
        self._responses += 1
        number = self._responses
        applied = body.get("tool_choice") or "auto"
        if self._follow_up_as == "ignores tool_choice":
            applied = "auto"
        output = []
        if applied != "none" and agent.acts_on_follow_up:
            _, returned = self._call_tool(state, "GET", "unrelated", {})
            call_id = f"call_fake_{number}_1"
            sent = json.dumps({"attempt_id": state, "todo_id": "unrelated"}, separators=(",", ":"))
            output.append({"type": "openapi_call", "id": f"fc_fake_{number}_1", "call_id": call_id,
                           "name": "todos_readTodo", "arguments": sent, "status": "completed"})
            output.append({"type": "openapi_call_output", "id": f"fco_fake_{number}_1", "call_id": call_id,
                           "name": "todos_readTodo", "output": json.dumps({"response": json.dumps(returned)}),
                           "status": "completed"})
        answer = agent.confidence.pop(0) if agent.confidence else "100"
        output.append({"type": "message", "id": f"msg_fake_{number}", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": answer, "annotations": []}]})
        reference = body.get("agent_reference") or {}
        response = {
            "id": f"resp_fake_{number}", "object": "response", "status": "completed", "error": None,
            "incomplete_details": None, "model": MODEL_DEPLOYMENT, "metadata": body.get("metadata", {}),
            "agent_reference": {"type": "agent_reference", "name": name,
                                "version": reference.get("version", agent.versions[-1])},
            "tool_choice": applied,
            "output": output,
            "usage": {
                "input_tokens": 800,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 802,
            },
        }
        if self._follow_up_as != "starts a new conversation":
            response["previous_response_id"] = previous
        return response

    def _failed_target(self, name: str, row: dict, message: str) -> dict:
        return {
            "datasource_item": {
                **row,
                "sample.output_text": None,
                "response_id": None,
                "agent_id": "",
                "agent_name": name,
                "agent_version": None,
                "conversation_id": None,
                "previous_response_id": None,
                "sample.tool_calls": [],
                "sample.tool_definitions": [],
                "sample.output_items": [],
                "trace_id": "",
                "span_id": "",
            },
            "sample": {
                "error": {"code": "TARGET_INVOCATION_FAILED", "message": message},
                "finish_reason": "",
                "input": [],
                "max_completion_tokens": None,
                "model": "",
                "output": [],
                "seed": None,
                "temperature": None,
                "top_p": None,
                "usage": None,
                "latency_ms": None,
            },
        }

    def _result(self, criterion: dict) -> dict:
        passed = not self._criterion_fails
        return {
            "type": criterion["type"],
            "name": criterion["name"],
            "metric": criterion["name"],
            "score": 1 if passed else 0,
            "label": "pass" if passed else "fail",
            "reason": None,
            "threshold": None,
            "passed": passed,
            "status": "completed",
        }

    def _per_model_usage(self, name: str, items: list[dict]) -> list[dict]:
        """One row for the target, and for a prompt agent one more for its deployment with the same numbers."""
        used = [item["sample"]["usage"] for item in items if item["sample"]["usage"]]
        if not used:
            return []
        row = {
            "invocation_count": len(used),
            **{key: sum(usage[key] for usage in used) for key in USAGE_FIELDS},
        }
        models = [MODEL_DEPLOYMENT] if self.agents[name].kind == "prompt" else []
        return [{"model_name": model, **row} for model in (*models, "azure_ai_agent_target")]

    def _call_tool(self, state, method, todo_id, arguments):
        """Call the to-do service as the agent's tool does. Returns the status and the decoded body."""
        service = self._todo_service
        request = urllib.request.Request(
            f"{service.url}/attempts/{state}/todos" + (f"/{todo_id}" if todo_id else ""),
            data=json.dumps(arguments).encode() if arguments else None,
            method=method,
            headers={"x-api-key": service.api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"null")
