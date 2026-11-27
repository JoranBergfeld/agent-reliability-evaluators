# Foundry prompt agent target

`FoundryPromptAgentTarget` lets the Evaluator sweep make its Attempts against a prompt agent in your Foundry project. It runs in your own process, from your code or your CI, under your identity. Scoring also happens in your process.

```python
from reliability_evaluators.agent_target.foundry_prompt_agent import FoundryPromptAgentTarget
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import Task, run_sweep

target = FoundryPromptAgentTarget(
    project_endpoint="https://<account>.services.ai.azure.com/api/projects/<project>",
    agent_name="my-prompt-agent",
)


def item_is_closed(attempt, response):
    closed = my_test_service.item_is_closed("todo-list/reliability-test")
    return TaskOutcome(success=closed, source="final state of the to-do test service")


scorecard = run_sweep(
    tasks=[Task(task_id="close", prompt="Close the item.", success_rule=item_is_closed, attempts=5)],
    agent_target=target,
    metrics=["outcome_consistency"],
)
```

`my_test_service` stands for your own code.

It is the sibling of the [Foundry hosted agent target](../foundry_hosted_agent/readme.md) and takes the same arguments, apart from `agent_version`. What differs between the two agent types is under [a failing tool fails the whole invocation](#a-failing-tool-fails-the-whole-invocation).

| Argument | Meaning | Default |
| --- | --- | --- |
| `project_endpoint` | The endpoint of the Foundry project the agent is in. | Required |
| `agent_name` | The name of the prompt agent. | Required |
| `agent_version` | The version of the agent that must answer. | The project chooses. The version that answered is recorded on each Attempt. |
| `credential` | A function that returns a bearer token for the project. | The Azure CLI |
| `compose_input` | A function of the Attempt request that returns what the agent is sent instead of the prompt. | The prompt is sent exactly as it is. |
| `timeout_seconds` | How long the target waits for the project when no time limit is declared for the Attempt. | 180 |
| `clock` | The clock the target times its own request on, in seconds. | `time.perf_counter` |

## What it needs

- Python 3.10 or later and the standard library. The target calls the project's REST API with `urllib`. It needs no Azure SDK and no other package, so copying `src/reliability_evaluators/` stays enough: see [ADR 0005](../adr/0005-foundry-targets-use-the-standard-library.md). The metric definitions in `evaluators/` and `evidence/` do not import the target.
- A bearer token for the resource `https://ai.azure.com`. The target sends no API key.
- Network access from where the sweep runs to the project endpoint.

### Authentication

By default the token comes from the Azure CLI: the target runs `az account get-access-token --resource https://ai.azure.com` and keeps the token until five minutes before it expires. So the sweep runs as whoever the Azure CLI is signed in as. This is the hosted agent target's `AzureCliCredential`, and everything its [documentation says about authentication](../foundry_hosted_agent/readme.md#authentication) holds here: signing the Azure CLI in from GitHub Actions or Azure Pipelines, `AzureCliCredential(subscription=...)`, and passing your own function, for example one built on `azure-identity`, as `credential`.

The identity needs a role in the Foundry project that allows it to invoke the agent. In the live check it held Foundry User on the Foundry account and Foundry Project Manager on the project. Which of the two is needed was not isolated.

Not verified: authentication in CI and a credential other than the Azure CLI. The live check ran on a workstation, signed in with `az login` as a user.

## What the agent is sent

One Responses request per Attempt, not streamed:

```text
POST <project endpoint>/openai/v1/responses
{"input": "<the Task's prompt>",
 "agent_reference": {"type": "agent_reference", "name": "<agent name>", "version": "<agent version>"},
 "metadata": {"attempt_id": "<Attempt identity>", "task_id": "<Task identity>"}}
```

The prompt is sent exactly as it is. `version` is sent only when you pass `agent_version`. The `metadata` identifies the Attempt on the platform. The response returns it, and it is recorded.

A prompt agent cannot be configured per Attempt. There are two ways to tell it which test data to work on:

- **Name the test resource in the Task's prompt**, and let your prepare and clean-up controls bring that one resource into its starting state for every Attempt. The prompt is then the same for every Attempt and nothing is composed. The live check does this.
- **Pass `compose_input`**, a function of the Attempt request that returns what the agent is sent instead of the prompt:

  ```python
  target = FoundryPromptAgentTarget(
      project_endpoint=...,
      agent_name=...,
      compose_input=lambda request: f"Use list {my_test_service.list_for(request.attempt_id)}. {request.prompt}",
  )
  ```

  The agent then no longer receives the prompt exactly as it is, so the composed input is recorded on the Attempt as it was sent. Under a prompt variation the prompt is the exact text of one of your prompt variants. Whatever `compose_input` puts around it has to be the same under the baseline and under every variant, or the comparison no longer measures the variant alone.

The request is sent once. The target never repeats a request that may have reached the agent. Retries are the sweep's, declared with `ExecutionLimits(max_retries=...)`, and every retry stays in the evidence: see [execution limits](../sweep/execution-limits.md).

### A new conversation is not a reset

Every invocation is one request that refers to no earlier response, so the agent starts a new conversation for every Attempt. That resets nothing outside the conversation: whatever the agent's tools changed stays changed.

The target never records a starting-state policy, and the sweep never infers one from it. State is fresh only when you declare `starting_state="fresh"` in an [experiment](../sweep/controlled-experiments.md) and your prepare control makes it so. Attempts of a Task given without an experiment record `"starting_state": null`.

## What is recorded for each Attempt

Every Attempt in the scorecard has an `invocation` entry with what the platform returned for it. It has the fields of the hosted agent target's entry where they apply, so both can be read the same way:

| Field | Contents |
| --- | --- |
| `agent_target`, `protocol`, `api_version` | `foundry_prompt_agent`, `responses`, `v1` |
| `entry_point` | `customer_code`: the request was sent from your process. The [cloud evaluation target](../foundry_cloud_evaluation/readme.md) records `foundry_cloud_evaluation` |
| `composed_input` | What the agent was sent, when `compose_input` composed it. `null` when the agent was sent the prompt as it is |
| `http_status` | The HTTP status of the answer |
| `request_ids` | The `x-request-id` and `apim-request-id` headers of the answer |
| `response_id` | The identifier of the response |
| `agent` | The agent reference of the response: the agent's name and the version that answered |
| `model`, `served_model` | The model deployment the response names, and the model the answer's `x-ms-served-model` header says served the request |
| `metadata` | The metadata the response returned: the Attempt identity and Task identity it was sent |
| `status`, `error` | The status and the error of the response. For an answer without a response, the error it carried |
| `token_usage` | The `usage` of the response, as the platform reported it |
| `token_usage_missing` | Why there is no usage, when there is none. Missing usage is never recorded as zero |
| `tool_calls` | The tool calls the response exposed, in order: `call_id`, `type`, `name`, `arguments`, the `output` the tool returned and the `status` of the call |

An Attempt whose invocation failed keeps the same entry with whatever the platform returned, so its request identifiers and any token usage are not lost. An Attempt for which no answer came back at all has `"invocation": null`.

A tool call is an output item of the response whose type ends in `_call`, paired by `call_id` with the item of the type `<that type>_output`. For an OpenAPI tool these are `openapi_call` and `openapi_call_output`, and the name is `<tool name>_<operationId>`, such as `todos_readTodo`. The arguments are decoded from the JSON text the protocol gives them as. The output is kept as the text the platform gave. Only `openapi_call` was observed live: other tool types follow the same rule without having been observed. `tool_calls` is `null` when the answer has no response, which is not the same as an empty list.

The token usage is recorded, not reported: a token usage report is a separate piece of work. The usage is also not given to the sweep as a resource measurement, because it has not been established that it covers every model call of an Attempt.

`invocation` is part of the attempt-level evidence, so it is in `evidence.json` and in the human-readable report, and a [recalculation](../sweep/late-and-corrected-outcomes.md) keeps it.

### Resource measurements

| Resource type | Unit | Measurement boundary |
| --- | --- | --- |
| Elapsed time | seconds | Wall clock from just before the Responses request is sent until its response has been read, measured by the target. Getting the token is not included. This replaces the sweep's own timing of the whole invocation |
| Tool calls | calls | The tool call items in the response's output, counted by the target. A tool call the project does not report there is not counted |

An Attempt whose invocation failed has neither.

### Action evidence

The trajectory metrics need a declared source of action evidence. To use the tool calls of the response as that source:

```python
from reliability_evaluators.agent_target.foundry_prompt_agent import NATIVE_TOOL_CALLS, native_tool_calls
from reliability_evaluators.sweep import ActionEvidenceDeclaration

action_evidence = ActionEvidenceDeclaration(
    evidence_source=NATIVE_TOOL_CALLS,
    record=native_tool_calls,
    action_types={"todos_readTodo": "read", "todos_updateTodo": "update"},
)
```

An action event from this source carries an `error` only when the platform gave the call a status other than `completed`. The tool calls of a response and the calls your service received can differ, and there is no default between them: you declare which one a metric is calculated from. An Attempt whose invocation failed has no action evidence from this source, although the agent may have acted.

## The task outcome never comes from the response

The target does not look at the agent's answer to decide anything. Your task-success rule decides the task outcome, and it should read independent evidence, such as the state of your test service.

A response with status `completed` and no error makes the Attempt's execution status `completed`. That says the agent ran. It says nothing about the Task.

## When the invocation fails

Each of these is an execution status of the Attempt with the reason in `execution_error`. None is a task failure: the Attempt has no task outcome, and an Evaluator leaves it out and counts it with a reason.

| What happened | Execution status | `execution_error` starts with |
| --- | --- | --- |
| The project answered with an HTTP error | `failed` | `InvocationFailed: the Foundry project answered HTTP <status>:` and the error's code and message |
| The response has a status other than `completed`, such as `failed` or `incomplete` | `failed` | `InvocationFailed: the response <id> of the prompt agent ended with status` |
| The response has status `completed` and carries an error | `failed` | `InvocationFailed: the response <id> of the prompt agent has status 'completed' and carries an error:` |
| The answer was not a response | `failed` | `InvocationFailed: the Foundry project answered HTTP 200 with a body that is not a response:` |
| The project could not be reached | `failed` | `InvocationFailed: the Foundry project could not be reached:` |
| No answer within the time limit | `timed_out` | `TimeoutError: the Foundry project did not answer within <limit> seconds` |
| No token could be obtained | `failed` | `RuntimeError: the Azure CLI gave no token for the Foundry project:` |

The time limit is the `attempt_timeout_seconds` you declare in the Task's `ExecutionLimits`. Without one it is the target's own `timeout_seconds`, 180 by default. An agent the project does not have, or a version it does not have, is answered with HTTP 404.

After a timeout the agent may already have acted. In the live observations below it had made one or two tool calls by then. Your clean-up control has to cope with that.

### A failing tool fails the whole invocation

This is where a prompt agent differs from a hosted agent. Observed live: when a tool of a prompt agent returns an HTTP error, the caller gets no response. The project answers the invocation with HTTP 400 and the error code `tool_user_error`. There is no response identifier, no output and no usage, only request identifiers. Only your service shows what the agent did before the fault. A hosted agent, in contrast, was observed to answer `completed` after a failed tool call.

Two consequences:

- The Attempt is an execution failure, not a task failure. That is what an invocation error is.
- **[Fault robustness](../fault_robustness/readme.md) is unavailable when the fault makes a tool of a prompt agent return an HTTP error**, because no Attempt under the fault completes. In the live check it was unavailable with the reason `no valid Attempt under the fault variation`. It is not 0. Whether a fault that a tool reports inside a successful HTTP answer reaches the agent, and so can be measured, was not tried.

## No confidence rating

The target declares that it does not support an isolated follow-up. Whether a Foundry prompt agent can be asked a follow-up question in the conversation of a completed Attempt, without being able to act, has not been verified. Until it is, every Attempt made through this target has no confidence rating, with that reason, and calibration, strict confidence discrimination and the Brier-based score are unavailable. The agent is not asked, and a rating in its task response is not used.

## Limits

- One request per Attempt, not streamed. A streamed response is not read.
- This target invokes the agent from your process only. To invoke it through Foundry cloud evaluation, use the [cloud evaluation target](../foundry_cloud_evaluation/readme.md), which also lists what differs between the two entry points.
- The target does not look the agent up. It does not check that the agent is a prompt agent, and it reports no agent configuration for you to declare.
- A conversation is never continued. The target has no way to link an invocation to an earlier response.

## Observed live

Observed on 2026-11-27 in Sweden Central, on the disposable fixture in [`live_fixture/`](../../live_fixture/README.md), from a workstation with Azure CLI 2.93.0 and Python 3.15, signed in as a user. The fixture's prompt agent runs `gpt-4.1-mini` (`2025-04-14`) with one OpenAPI tool. This records what happened. No documentation was consulted for it, and none of it is a reliability result for any agent. Synthetic data only.

The interface as it worked on that date:

| Purpose | Request |
| --- | --- |
| Invoke the prompt agent | `POST <project endpoint>/openai/v1/responses`, with the version in the path and no `api-version`. The answer's `api-supported-versions` header read `1.0,2025-05-15-preview,2025-11-15-preview` |

### The live check

`tests/live/test_foundry_prompt_agent_live.py` runs one sweep from this source against that agent, scoring outcome consistency and fault robustness. The experiment declares continuing state, with five Attempts under the baseline and five under a fault on the update tool. The prompt names one state of the to-do service, and the agent is asked to do the whole Task in every Attempt. It ran twice on one fixture, with the target and the check as they are here, and passed both times with the same results. Two things were added after that, and the check was not run again: the customer-process rescore of a Foundry-managed result, which the check does not use, and the `entry_point` of the invocation evidence, a constant the target adds to what it records.

- **Outcome consistency was 0.36 in the scorecard and 0.36 calculated independently.** The check decides each task outcome itself, from the events and the final state the service had recorded when the Attempt ended, which the verify control reads. The first baseline Attempt left the service with exactly the five operations of the Task and succeeded. Each later one added five more and failed the strict task-success rule. p = 1/5, (2 × 0.2 − 1)² = 0.36. The scorecard used the same five Attempts and had the same five task outcomes.
- **A new conversation did not reset the service.** Every Attempt was a response of its own. The service held 5, 10, 15, 20 and 25 operations after the five baseline Attempts. Had each new conversation been a reset, every Attempt would have succeeded and the value would have been 1.
- **Each Attempt was identifiable.** Every completed response had its own `id`. The response returned the `metadata` it was sent. Both request identifier headers were present, and so were the agent version `1`, the model deployment and the served model `gpt-4.1-mini-2025-04-14`.
- **Tool calls were exposed in order** as `openapi_call` and `openapi_call_output` items, five in each completed Attempt. In every one the tool calls of the response were the calls the service received during the Attempt, in the same order.
- **Token usage was reported on every completed response**: 798 to 821 total tokens. Whether it covers every model call was not established.
- **The request took 5.6 to 11.4 seconds**, by the target's own timing.
- **Each Attempt under the fault was answered with HTTP 400 `tool_user_error`** and recorded as `failed`, with no task outcome, no usage with the reason for that, and no resource measurement. During each, the service recorded four successful calls and then the update, answered with 503 by the injected fault. The verify control observed the fault from those events.
- **Fault robustness was unavailable**, with the reason `no valid Attempt under the fault variation`.

### Single invocations through the target, outside the check

- **Time limit.** With a limit of 1 second, set on the target, and of 1.5 seconds, declared for the Attempt, the target raised `TimeoutError`. Twenty seconds later the service had recorded one call for the first and two for the second, and no more: the agent had acted before the caller stopped waiting, and did not finish.
- **Pinned version.** With `agent_version="1"`, version `1` answered. With a version that does not exist, the project answered HTTP 404 `not_found`.
- **Composed input.** With a prompt that names no state and a `compose_input` that puts one in front of it, the agent worked on that state, the Task succeeded, and the Attempt recorded the input as it was sent.
- **The token call is not in the target's timing.** One invocation took 12.1 seconds including the call to the Azure CLI, and 7.2 by the target's own timing.

### Single requests, not through the target

Made on the first fixture.

- A request with an invalid token was answered with HTTP 401 and the error code `401`, with an `apim-request-id` header and no `x-request-id`.
- A response has no `conversation` and no `previous_response_id`.

### Earlier forms of the check

The check was run in two earlier forms on earlier fixtures, five times in all, each time passing with 0.36 in the scorecard and 0.36 calculated independently, and with the same findings for completed and faulted Attempts. The first form, run three times, used fresh state with a state of the service per Attempt, named by text put in front of the prompt, and sent one of five baseline Attempts the Task without its last step. The second, run twice, was the check as it is now, before the evidence of an invocation took the form it shares with the hosted agent target.

### Resources

Four resource groups were created for this work, all in the subscription named `Sandbox`:

| Resource group | What it was for |
| --- | --- |
| `rg-are-43-4844d30a` | The fixture of the first form of the check and of the single requests. |
| `rg-are-43-791299ee` | Never became a fixture. Its model deployment was refused with `(InvalidResourceProperties) Failed to validate policies for model gpt-4.1-mini/2025-04-14`. Provisioning again a minute later, with nothing changed, succeeded. The cause is not known. |
| `rg-are-43-7ec9c0dc` | The fixture of the second form of the check. |
| `rg-are-43-8cfafd0b` | The fixture of the check as it is here, and of the single invocations through the target. |

Each was deleted with the fixture's `teardown`, the second by the provisioning that failed, and the soft-deleted Foundry accounts were purged. Afterwards a separate `az group exists` returned `false` for each of the four. Deleting a group that held a whole fixture took 27 to 29 minutes.

### Not verified

- The self-contained form of the live check, in which it provisions and tears down its own fixture. The check was run on fixtures provisioned beforehand with `python -m live_fixture provision`.
- A response that arrives with a status other than `completed`, an answer that is not a response, and a project that cannot be reached. The default test run covers them against a fake project that answers in the shapes above.
- Authentication in CI or with `azure-identity`.
- Tool types other than OpenAPI, a second version of the agent, and which roles are really required.
- What the platform does with an invocation whose caller stopped waiting, beyond the observations above.
- More than one Attempt at a time and the confidence follow-up. Foundry cloud evaluation as the entry point has its own target and its own [live observations](../foundry_cloud_evaluation/readme.md#observed-live).
