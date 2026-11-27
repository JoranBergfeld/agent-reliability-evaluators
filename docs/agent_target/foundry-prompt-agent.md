# A Foundry prompt agent as the agent target

Point the Evaluator sweep at a Foundry prompt agent you have deployed. The sweep invokes it from your own code or CI, under your own identity, through the Responses interface of your Foundry project.

| | |
| --- | --- |
| Class | `reliability_evaluators.agent_target.FoundryPromptAgentTarget` |
| Entry point | Your own code. Foundry cloud evaluation is not covered here. |
| Dependencies | The Python standard library. No Azure SDK. See [ADR 0005](../adr/0005-foundry-targets-use-the-standard-library.md). |
| Identity | Yours: a bearer token for `https://ai.azure.com`, by default from the Azure CLI. |
| Confidence follow-up | Declared unsupported. Calibration, strict confidence discrimination and the Brier-based score are unavailable on this target. |

## Using it

```python
from reliability_evaluators.agent_target import FoundryProject, FoundryPromptAgentTarget
from reliability_evaluators.sweep import run_sweep

target = FoundryPromptAgentTarget(
    project=FoundryProject("https://<account>.services.ai.azure.com/api/projects/<project>"),
    agent_name="todo-agent",
    agent_version="3",  # optional
)

scorecard = run_sweep(experiments=[experiment], agent_target=target, metrics=["outcome_consistency"])
```

| Argument | Meaning | Default |
| --- | --- | --- |
| `project` | The Foundry project the agent is in, with the identity to call it as. | Required |
| `agent_name` | The name of the prompt agent. | Required |
| `agent_version` | The version of the agent that must answer. | The project chooses. The version that answered is recorded on each Attempt. |
| `timeout_seconds` | How long the target waits for the project when you declared no time limit for the Attempt. | 180 |

When a Task declares `ExecutionLimits(attempt_timeout_seconds=...)`, the target waits that long instead. Either way it then raises `TimeoutError`, and the Attempt is `timed_out`: see [execution limits](../sweep/execution-limits.md).

The target sends each invocation once. It never retries. Retries are yours to declare per Task, and every retry is a visible Attempt.

### The prompt is sent exactly as it is

The target sends the Task's prompt, or the exact text of a prompt variant, as the `input` of the request. It adds nothing and rewrites nothing.

A prompt agent cannot be configured per Attempt. If its tools have to know which test resource to work on, name that resource in the Task's prompt, and let your prepare and clean-up controls bring that one resource into its starting state for every Attempt. The live check does this: its prompt names one state of the to-do service, which the controls create and remove.

## Authenticating, also in CI

The target needs a bearer token whose audience is `https://ai.azure.com`, for an identity that may call the agent. In the live check that identity had the role Foundry User (`53ca6127-db72-4b80-b1b0-d745d6d5456d`) on the Foundry account and Foundry Project Manager (`eadc314b-1a2d-4efa-be10-5d325db5065e`) on the project. Whether the first alone is enough was not isolated.

`FoundryProject(endpoint)` asks the Azure CLI: `az account get-access-token --resource https://ai.azure.com`. So whatever signs the Azure CLI in works:

| Where | How the Azure CLI gets its identity |
| --- | --- |
| Your machine | `az login` |
| GitHub Actions | The `azure/login` action with a federated credential (OIDC), before the step that runs the sweep. No secret is stored. |
| Azure DevOps | An `AzureCLI` task with a service connection, running the sweep as its script. |
| Anywhere else | `az login --service-principal`, or `az login --identity` on a host with a managed identity. |

Only the first row was run here. The others are how the Azure CLI is documented to sign in, and were not tried.

The token is asked for again when it is within five minutes of the expiry the CLI gave, so a sweep can run longer than a token lives. It is held in memory only and is never written to the scorecard.

`FoundryProject` here is `reliability_evaluators.agent_target.FoundryProject`. [Foundry-managed scoring](../managed_scoring/readme.md) has a class of the same name for the same project, and the two are not interchangeable yet.

To use another source, pass any function that returns a token:

```python
from azure.identity import DefaultAzureCredential  # your own dependency, not one of this source

credential = DefaultAzureCredential()
project = FoundryProject(endpoint, token=lambda: credential.get_token("https://ai.azure.com/.default").token)
```

The function is called before every request, so it decides how long a token is reused. This form was not run here.

## What each Attempt records

The Attempt in the scorecard carries `platform_evidence`: what the project returned about the invocation, as returned.

```json
{
  "source": "Foundry Responses API, called from customer code",
  "identifiers": {
    "response_id": "resp_0819b0d3…",
    "status": "completed",
    "agent_reference": {"type": "agent_reference", "name": "todo-agent", "version": "3"},
    "model": "my-gpt-41-mini",
    "served_model": "gpt-4.1-mini-2025-04-14",
    "metadata": {"attempt_id": "close-item/baseline/attempt-1", "task_id": "close"},
    "request_id": "…",
    "apim_request_id": "…"
  },
  "token_usage": {
    "input_tokens": 643,
    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
    "output_tokens": 239,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 882
  },
  "token_usage_missing": null,
  "tool_calls": [
    {"tool_name": "todos_readTodo", "arguments": "{\"todo_id\":\"target\"}", "result": "{\"response\": \"…\"}", "error": null}
  ]
}
```

| Field | Where it comes from |
| --- | --- |
| `response_id`, `status`, `agent_reference`, `model`, `metadata` | The response body: `id`, `status`, `agent_reference`, `model`, `metadata`. `model` is the name of the model deployment. |
| `served_model`, `request_id`, `apim_request_id` | The response headers `x-ms-served-model`, `x-request-id` and `apim-request-id`. |
| `token_usage` | The `usage` of the response, unchanged. |
| `token_usage_missing` | Why there is no usage, when there is none. Usage that was not reported is never recorded as zero. |
| `tool_calls` | The tool calls in the `output` of the response, in order. `null` means the project exposed none for this invocation, which is not the same as an invocation known to have made no call. |

An identifier the project did not return is left out.

**Each Attempt is identifiable on both sides.** The target sends the identity of the Attempt and of its Task as the `metadata` of the request. The project returns it with the response, so the response in your scorecard and the response in your Foundry project name the same Attempt.

**Tool calls.** A tool call is an `output` item whose type ends in `_call`. What it returned is the item of the type `<that type>_output` with the same `call_id`. For an OpenAPI tool these are `openapi_call` and `openapi_call_output`, and the tool name is `<tool name>_<operationId>`, such as `todos_readTodo`. The arguments and the result are kept as the project gave them, which is as JSON text. Only `openapi_call` was observed live: other tool types follow the same rule without having been observed.

**Tool-call count.** The number of those tool calls is reported as the resource measurement `tool_calls`, with the unit `calls` and the boundary `tool calls in the output of the Foundry response, counted by the agent target`. [Resource consistency](../resource_consistency/readme.md) uses it. An Attempt whose invocation failed has no count.

**Token usage is captured, not reported.** It is on each Attempt for a later token usage report. The target does not return it as a resource measurement, so resource consistency does not use it.

`platform_evidence` is part of the attempt-level evidence, so it is in `evidence.json` and in the human-readable report, and a [recalculation](../sweep/late-and-corrected-outcomes.md) keeps it.

### Using the platform's tool calls as action evidence

The trajectory metrics need a declared source of action evidence. To calculate them from the tool calls the project exposed:

```python
from reliability_evaluators.agent_target import platform_tool_calls
from reliability_evaluators.sweep import ActionEvidenceDeclaration

action_evidence = ActionEvidenceDeclaration(
    evidence_source="tool calls exposed by the Foundry project",
    record=platform_tool_calls,
    action_types={"todos_readTodo": "read", "todos_updateTodo": "update"},
)
```

This is one possible source, not a default. The calls the project exposes and the calls your service received can differ, and they can give different metric values. An Attempt whose invocation failed exposes no tool calls, so it has no action evidence from this source, although the agent may have acted.

## When the invocation fails

Each of these is an execution status of the Attempt, with the reason in `execution_error`. None of them is a task failure: the Attempt has no task outcome, and Evaluators leave it out and count it.

| What happened | `execution_status` | `platform_evidence` |
| --- | --- | --- |
| The project answered with an HTTP error status. | `failed` | `http_status`, `error_code`, `error_request_id`, `request_id`, `apim_request_id`, as far as returned. No usage, with the reason. |
| The project answered HTTP 200 with a body that is not a response. | `failed` | `http_status` and the request identifiers. No usage, with the reason. |
| The response arrived with a `status` other than `completed`, or carrying an `error`. | `failed` | The whole evidence of the response, its usage and tool calls included. |
| No answer within the time limit. | `timed_out` | None. |
| The request could not be sent, or the token could not be obtained. | `failed` | None. |

A response that arrives is not by that alone a completed one: the target reads its `status` and its `error`.

Usage that a failed response did report is kept, because it was spent.

### A failing tool fails the whole invocation

Observed live: when a tool of a prompt agent returns an HTTP error, the caller gets no response. The project answers the invocation with HTTP 400 and the error code `tool_user_error`. There is no response identity, no output and no usage, only request identifiers. Only your service shows what the agent did before the fault.

Two consequences:

- The Attempt is an execution failure, not a task failure. That is what this target is required to record for an invocation error.
- **[Fault robustness](../fault_robustness/readme.md) is unavailable when the fault makes the tool return an HTTP error**, because no Attempt under the fault completes. In the live check it was reported as unavailable with the reason `no valid Attempt under the fault variation`. It is not reported as 0. Whether a fault that your tool reports inside a successful HTTP answer reaches the agent, and so can be measured, was not tried.

## State is not reset

Every invocation is one request that refers to no earlier response, so the agent starts a new conversation for every Attempt. That says nothing about anything outside the conversation. Whatever the agent's tools changed stays changed.

The target never records a starting-state policy and the sweep never infers one from it. State is fresh only when you declare `starting_state="fresh"` in an [experiment](../sweep/controlled-experiments.md) and your prepare control makes it so. Attempts of a Task given without an experiment record `"starting_state": null`.

## Confidence follow-up

The target declares `IsolatedFollowUp(supported=False)`, with the reason `a confidence-only follow-up in which a Foundry prompt agent cannot act has not been verified`. The sweep therefore sends no confidence request, every Attempt records that reason, and the three predictability metrics are unavailable. A rating inside the agent's answer is never used instead.

## Not covered

- Foundry cloud evaluation as the entry point.
- Streaming. The target sends one request and reads one complete response.
- Concurrency. The live check ran its Attempts one after another.
- A continued conversation. The target has no way to link an invocation to an earlier response.

## Observed live

Observed on 2026-11-27 in Sweden Central, on the disposable fixture in [`live_fixture/`](../../live_fixture/README.md): a prompt agent on `gpt-4.1-mini` version `2025-04-14` with one OpenAPI tool, called with Azure CLI 2.93.0 and Python 3.15. This section records what happened, not what the platform documents. No documentation was consulted for it. Synthetic data only.

| Purpose | Request, as it worked on that date |
| --- | --- |
| Token | `az account get-access-token --resource https://ai.azure.com`. The output had `accessToken` and `expires_on`. |
| Invoke the prompt agent | `POST <project endpoint>/openai/v1/responses`, with the version in the path and no query string, and with `input`, `agent_reference` and `metadata` in the body. The response header `api-supported-versions` read `1.0,2025-05-15-preview,2025-11-15-preview`. |

### The live check

`tests/live/test_foundry_prompt_agent_live.py` runs one sweep through `run_sweep` and this target, scoring outcome consistency and fault robustness. The experiment declares continuing state, with five Attempts under the baseline and five under a fault on the update tool. The agent is asked to do the whole Task in every Attempt. It ran twice on the code as it is here and passed both times, with the same result.

- **Outcome consistency matched an independent calculation.** The check decides each task outcome itself, from the events and the final state the service had recorded when the Attempt ended, which the verify control reads. The first baseline Attempt left the service with exactly the five operations of the Task and succeeded. Each later one added five more and failed the strict task-success rule. p = 1/5, (2 × 0.2 − 1)² = 0.36. The scorecard reported 0.36, from the same five Attempts and with the same five task outcomes.
- **A new conversation did not reset the service.** Every Attempt was a response of its own. The service held 5, 10, 15, 20 and 25 operations after the five baseline Attempts. Had each new conversation been a reset, every Attempt would have succeeded and the value would have been 1.
- **Each completed Attempt** had a `response_id` of its own, the agent version `1`, the model deployment, the served model `gpt-4.1-mini-2025-04-14`, the returned `metadata` naming the Attempt, and usage of 782 to 823 total tokens. The tool calls the project exposed were the calls the service received during the Attempt, in the same order, and the tool-call count was their number. An Attempt took 4.3 to 10.8 seconds, the first one including the call to the Azure CLI.
- **Each Attempt under the fault** was answered with HTTP 400 `tool_user_error` and recorded as `failed`, with no task outcome, no usage and the reason for that. During each, the service recorded four successful calls and then the update, answered with 503 by the injected fault. The verify control observed the fault from those events.
- **Fault robustness** was unavailable, with the reason `no valid Attempt under the fault variation`.

An earlier form of the check ran three times before that, on an earlier fixture and with the same findings for completed and faulted Attempts. It used fresh state with a state of the service per Attempt, and sent one of five baseline Attempts the Task without its last step: four succeeded, one failed, and the scorecard reported 0.36 as the check calculated. That form put text of its own in front of the prompt, which the target no longer allows.

### Single invocations through the target, outside the check

Made on the earlier fixture.

- **Time limit.** With a limit of 1 second, set on the target, and of 1.5 seconds, declared for the Attempt, the target raised `TimeoutError` after that long. Twenty seconds later the service had recorded two calls for the first and one for the second, and no more: the agent had acted before the caller stopped waiting, and did not finish. So an Attempt that timed out can leave changes behind, and your clean-up control has to cope with them. Getting a token from the Azure CLI is not counted in the target's limit: the first invocation took 3.7 seconds in all.
- **Pinned version.** With `agent_version="1"`, version `1` answered. With a version that does not exist, the project answered HTTP 404 `not_found`, with the identity of the request in `error.additionalInfo.request_id`.

### Single requests, not through the target

- A request with an invalid token was answered with HTTP 401 and the error code `401`.
- A response has no `conversation` and no `previous_response_id`.

### Resources

Three resource groups were created for this work, all in the subscription named `Sandbox`:

| Resource group | What it was for |
| --- | --- |
| `rg-are-43-4844d30a` | The fixture of the earlier form of the check and of the single invocations and requests. |
| `rg-are-43-791299ee` | Never became a fixture. Its model deployment was refused with `(InvalidResourceProperties) Failed to validate policies for model gpt-4.1-mini/2025-04-14`. Provisioning again a minute later, with nothing changed, succeeded. The cause is not known. |
| `rg-are-43-7ec9c0dc` | The fixture of the check as it is here. |

Each was deleted with the fixture's `teardown`, the second by the provisioning that failed, and the soft-deleted Foundry accounts were purged. Afterwards a separate `az group exists` returned `false` for each of the three. Deleting a group that held a whole fixture took 27 and 29 minutes.

### Not verified

- The self-contained form of the live check, in which it provisions and tears down its own fixture. The check was run on fixtures provisioned beforehand with `python -m live_fixture provision`.
- A response that arrives with a status other than `completed`. The target's handling of it is tested on the fake project only.
- Signing in from GitHub Actions or Azure DevOps, and a token function other than the Azure CLI.
- Tool types other than OpenAPI, a second version of the agent, and which roles are really required.
- What the platform does with an invocation whose caller stopped waiting, beyond the two observations above.
