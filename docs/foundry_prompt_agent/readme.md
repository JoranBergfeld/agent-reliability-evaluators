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

It is the sibling of the [Foundry hosted agent target](../foundry_hosted_agent/readme.md) and takes the same arguments, apart from `agent_version`. What differs between the two agent types is under [a failing tool fails the whole invocation](#a-failing-tool-fails-the-whole-invocation) and [the confidence rating](#the-confidence-rating).

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
| `token_usage_source` | Where the usage comes from, when there is one: `the usage of the response the Foundry project returned for the prompt agent`. The usage report repeats it as the `source` of the count |
| `tool_calls` | The tool calls the response exposed, in order: `call_id`, `type`, `name`, `arguments`, the `output` the tool returned and the `status` of the call |

An Attempt whose invocation failed keeps the same entry with whatever the platform returned, so its request identifiers and any token usage are not lost. An Attempt for which no answer came back at all has `"invocation": null`.

A tool call is an output item of the response whose type ends in `_call`, paired by `call_id` with the item of the type `<that type>_output`. For an OpenAPI tool these are `openapi_call` and `openapi_call_output`, and the name is `<tool name>_<operationId>`, such as `todos_readTodo`. The arguments are decoded from the JSON text the protocol gives them as. The output is kept as the text the platform gave. Only `openapi_call` was observed live: other tool types follow the same rule without having been observed. `tool_calls` is `null` when the answer has no response, which is not the same as an empty list.

The token usage is recorded here as the platform reported it, and the scorecard reports it from here: per Attempt, per Evaluator and for the sweep, see [token usage](../token_usage/readme.md). The usage is not given to the sweep as a resource measurement. When the target was written it had not been established that it covers every model call of an Attempt. It has been compared since: in every invocation that was looked at on its own, the usage of the response was what the model deployment metered, to the token, with one model request per invocation whatever the number of tool calls. See [what was observed](../token_usage/readme.md#observed-live). The target still gives no such measurement.

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

## The confidence rating

When a selected metric uses confidence, the sweep asks the agent how likely it thinks the task succeeded, in one separate request after each completed Attempt: see [how the confidence rating is collected](../brier_based_score/readme.md#how-the-confidence-rating-is-collected). The target declares that it can ask this in an isolated follow-up, so calibration, strict confidence discrimination and the Brier-based score can be scored for a prompt agent. Give each Task its `shareable_success_rule`. Nothing else is needed.

```text
POST <project endpoint>/openai/v1/responses
{"input": "<the confidence question, which holds the Task's success rule in words>",
 "previous_response_id": "<the response of the Attempt>",
 "agent_reference": {"type": "agent_reference", "name": "<agent name>", "version": "<the version that answered the Attempt>"},
 "tool_choice": "none",
 "metadata": {"attempt_id": "<Attempt identity>", "task_id": "<Task identity>", "request": "confidence"}}
```

Isolated means that the agent answers in the conversation of that one Attempt, and that it cannot act while it answers.

- **The conversation.** `previous_response_id` names the response of the Attempt. The project then gives the agent what that response was made from: the input the agent was sent, its tool calls with what they returned, and its answer. The question is all that is added. The task outcome is not in the request, and neither is anything of another Attempt.
- **No action.** `tool_choice` is `none`. The project applies it when it runs the agent, and no tool call of the agent then gets through. It is a setting of the request, not an instruction to the agent. The question also tells the agent not to act, and nothing depends on the agent obeying that.

The request is sent once, to the version of the agent that answered the Attempt. It is a response of its own in your project. The response of the Attempt was the same when it was read again after its follow-ups.

### What the isolation rests on

A caller cannot take a tool away from a prompt agent for one request. A `tools` list sent with the request was accepted and ignored, and the agent called its tool. What a caller can set is the tool choice. So the isolation rests on the Foundry project applying `tool_choice: none`. Three things support that, and they are different kinds of evidence:

- **Documented.** Microsoft describes the setting for Foundry Agent Service as "`none`: The model doesn't call tools" ([Tool best practices for Microsoft Foundry Agent Service](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/tool-best-practice), read on 2026-11-27).
- **Observed.** A live agent was told, in such a follow-up, to create an item. Every time, it answered that it was creating the item and the service received no call. Told the same thing in the same conversation without the tool choice, it created the item, every time. The counts are under [the confidence follow-up](#the-confidence-follow-up).
- **Checked on every follow-up.** The project says in its response which response the follow-up continued, which tool choice it ran under and which tools were called. The target reads all three. It returns the agent's answer only when the follow-up continued the response of the Attempt, ran under `none` and holds no tool call.

The observations show that no call got through when the agent tried to make one. They do not show that none ever can. That rests on what the setting is documented to do. The check on each follow-up cannot undo a call. It makes sure that an answer given after one is never read as a rating.

What was not examined: tool types other than an OpenAPI tool and the code interpreter, a model other than `gpt-4.1-mini`, and a tool that the platform runs without a tool call of the model. See [not verified](#not-verified).

### When there is no rating

In each of these the Attempt keeps its execution status and its task outcome, the agent is not asked again, and the three metrics count the Attempt as missing with the reason. A rating that the agent wrote in its task response is never used. There is no fallback.

| What happened | `confidence.unavailable_reason` starts with |
| --- | --- |
| The agent's definition specifies a `tool_choice` of its own. The project refuses the follow-up before the agent runs. Observed live | `the confidence request failed: InvocationFailed: the Foundry project answered HTTP 400: invalid_payload: ToolChoice must match the agent's tool_choice when agent specifies tool_choice.` |
| The project answered the follow-up with another HTTP error, such as 429 for a rate limit | `the confidence request failed: InvocationFailed: the Foundry project answered HTTP <status>:` and the error's code and message |
| The response of the follow-up has a status other than `completed`, or carries an error | `the confidence request failed: InvocationFailed: the response <id> of the prompt agent` |
| No answer within the target's `timeout_seconds` | `the confidence request failed: TimeoutError: the Foundry project did not answer within <limit> seconds` |
| The response of the follow-up holds a tool call | `the confidence request failed: InvocationFailed: the agent called a tool while it answered the follow-up (<the tools>). The follow-up was not isolated, so its answer is not used` |
| The project reports another tool choice than `none` for the follow-up | `the confidence request failed: InvocationFailed: the Foundry project reports the tool choice '<it>' for the follow-up and not 'none'. The agent could act, so its answer is not used` |
| The project does not report that the follow-up continued the response of the Attempt | `the confidence request failed: InvocationFailed: the Foundry project does not report that the follow-up continued the response <id> of the Attempt, so its answer is not used` |
| The agent answered with anything but one number from 0 to 100 | See [how the answer is read](../brier_based_score/readme.md#how-the-answer-is-read) |

The last three of the `InvocationFailed` rows were never observed live. The default test run covers them against a fake project.

**An agent whose definition specifies a `tool_choice` has no rating on any Attempt**, so the three metrics are unavailable for it. The project runs such an agent only under the tool choice of its definition. In the live observations an agent defined with `tool_choice: required`, asked the confidence question without a tool choice in the request, read an item of the service before it answered. The question told it not to call a tool.

The time limit you declare for an Attempt does not apply to the follow-up. The target waits its own `timeout_seconds` for it.

### What is recorded for the follow-up

What the platform returned for the follow-up is kept on the Attempt as `confidence_invocation`, apart from `invocation`. It is `null` when no follow-up was sent. It has the fields of `invocation` without `composed_input`, and three more:

| Field | Contents |
| --- | --- |
| `input` | The question as the agent was sent it, with the success rule |
| `previous_response_id` | The response the project says the follow-up continued: the `response_id` of the Attempt's `invocation` |
| `tool_choice` | The tool choice the project says the follow-up ran under. `none` for a follow-up whose answer was used |
| `response_id`, `request_ids`, `agent`, `model`, `served_model`, `metadata`, `http_status`, `status`, `error` | As for an invocation. `metadata` holds the Attempt identity, the Task identity and `"request": "confidence"` |
| `token_usage`, `token_usage_missing` | The `usage` of the follow-up's response, or why there is none |
| `tool_calls` | The tool calls the response exposed. An empty list for a follow-up whose answer was used |

A follow-up that failed keeps the same entry with whatever the platform returned. The token usage of the follow-up is reported with its Attempt and apart from the invocation: see [token usage](../token_usage/readme.md#confidence-follow-ups). It is not part of the Attempt's resource measurements.

## Limits

- One request per Attempt, and one more for its confidence follow-up when a selected metric uses confidence. Neither is streamed, and a streamed response is not read.
- This target invokes the agent from your process only. To invoke it through Foundry cloud evaluation, use the [cloud evaluation target](../foundry_cloud_evaluation/readme.md), which also lists what differs between the two entry points.
- The target does not look the agent up. It does not check that the agent is a prompt agent, and it reports no agent configuration for you to declare.
- An Attempt never continues a conversation. Only the confidence follow-up names an earlier response, and that is the response of its own Attempt.
- The confidence follow-up needs the project to have kept the response of the Attempt. It had, every time, without the target asking for it. A project that does not keep responses was not tried.

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
- **Token usage was reported on every completed response**: 798 to 821 total tokens. Whether it covers every model call was not established here. It was compared with the deployment's own metrics later: see [token usage](../token_usage/readme.md#observed-live).
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

### The confidence follow-up

Observed on 2026-11-27 in Sweden Central, on fixtures of the same kind, from a workstation with Azure CLI 2.93.0 and Python 3.15, signed in as a user. The prompt agent ran `gpt-4.1-mini` (`2025-04-14`). This records what happened. It is not a reliability result for any agent. Synthetic data only.

Whether the agent acted was read from the to-do service: the calls it received and its state, just before each follow-up was sent and just after it ended. A follow-up that asks for a rating shows only that no call occurred. So the agent was also told, in follow-ups, to make a call. That shows whether one gets through.

The interfaces as they worked on that date:

| Purpose | Request |
| --- | --- |
| The Attempt and the follow-up | `POST <project endpoint>/openai/v1/responses`, with the version in the path and no `api-version`. The answer's `api-supported-versions` header read `1.0,2025-05-15-preview,2025-11-15-preview` |
| What a response was given as input | `GET <project endpoint>/openai/v1/responses/{response id}/input_items` |
| Another agent for a probe | `POST <project endpoint>/agents/{name}/versions?api-version=v1` |

#### The live check of the follow-up

`tests/live/test_confidence_follow_up_live.py` ran three times, each time on a fixture of its own: once on a fixture that was kept for the work, and twice self-contained, where the check provisions its fixture, runs and tears it down. The first two runs were made before token usage reporting was merged into the sweep, on a source that kept what the platform returned for a follow-up inside the confidence evidence. The third was made after that merge, with the follow-up kept as `confidence_invocation`. The cloud evaluation target was merged after the third run. It added the `entry_point` field to what is recorded, which the check does not read. The requests the target sends were the same in all three runs and are the same now. Every run passed, with the same findings:

- **The service recorded nothing during any follow-up.** In each run one sweep made five Attempts, three of the fixture's Task and two of that Task without its last step, and the agent was asked for a rating after each. For all 15 follow-ups, the calls the service had received and its state were the same before and after.
- **Each follow-up was a response of its own that continued the response of its Attempt.** The project reported the Attempt's response as `previous_response_id`, the tool choice `none`, no tool call, the agent version that had answered the Attempt, and the `metadata` it was sent.
- **The agent was given the conversation of its Attempt and the question, and nothing else.** The project listed as the input of each follow-up: the input of the Attempt, each tool call of the Attempt with what it returned, the agent's answer, and the question with the success rule, word for word. That was 13 items after an Attempt of five tool calls and 11 after one of four. No item held the task outcome or anything of another Attempt.
- **Every follow-up was answered with one number.** The three Attempts of the whole Task succeeded, and the agent rated each `100`, in every run. The two Attempts without the last step failed. The agent rated them `20` and `80` in the first run, and `80` both times in the other two.
- **Calibration, strict confidence discrimination and the Brier-based score were available**, and each equalled the value the check calculates itself, from the ratings and from the task outcomes it reads from the service: 0.8, 1 and 0.864 in the first run, and 0.68, 1 and 0.744 in the other two. They are the values of a synthetic Task and are recorded to show that the calculation ran on live ratings.
- **Token usage was reported on every follow-up**: 804 to 900 total tokens, of which 2 were output.
- **Told to act, the agent called nothing.** In each run one more Attempt was followed three times, through the target, by an instruction to create an item in the place of the question. All nine times the project reported the tool choice `none` and no tool call, the agent answered in many messages that it was creating the item, and the service received no call. Then the same instruction was sent once in the same conversation without a tool choice: the project reported `auto`, the agent called its create tool, and the service recorded the new item. That happened in each of the three runs.
- **A prompt agent whose definition specifies `tool_choice: required` was refused the follow-up.** Its two Attempts in each run completed. Each follow-up was answered with HTTP 400 `invalid_payload`, "ToolChoice must match the agent's tool_choice when agent specifies tool_choice", and the service recorded nothing: the project refused the request before the agent ran. No Attempt had a rating, and the three metrics were unavailable with the reason `no valid confidence/outcome pair`.

#### By hand, not through the target

Made on the first fixture with a throwaway script, as single requests. Each follow-up named the response of an Attempt of the fixture's prompt agent in `previous_response_id`, unless it says otherwise.

| What was sent | What happened |
| --- | --- |
| The confidence question, the same agent, `tool_choice: none` | Answered `100`, with no call at the service, in about one and a half seconds. 5 of 5, two of them with the agent version pinned and one with `store: false` |
| A question about the Attempt: which identifier it was told to use, and which title the tool returned | Answered with both. The title was in no prompt, only in what the tool had returned |
| The same question without `previous_response_id` | The agent answered that it had been told no identifier |
| An instruction to create an item, the same agent, `tool_choice: none` | No call at the service and no tool call in the response, in 27 of 27 follow-ups that completed. Each held 6 to 20 messages in which the agent wrote that it was creating the item. 15 more, sent without a pause, were answered HTTP 429 `rate_limit_exceeded` for the model deployment, also without a call |
| The same instruction, the same agent, no tool choice | The agent created the item, 8 of 8. The project reported the tool choice `auto` |
| The same instruction, the same agent, `"tools": []` | Accepted with HTTP 200 and ignored: the response listed the agent's own tool, and the agent created the item. Once |
| The same instruction, `tool_choice: none` and `max_tool_calls: 0` | HTTP 400 `integer_below_min_value` |
| The same instruction to another prompt agent with the same model and instructions and no tools | Accepted. That agent knew the conversation. It answered as if it had made the call, and called nothing. Once |
| The same instruction to the model deployment, with no agent and no tools | Accepted. The model knew the conversation, answered as if it had made the call, and called nothing. Once. Sent the confidence question and the agent's instructions, it answered `100` |
| A follow-up with `tool_choice: none` to an agent defined with `tool_choice: required` | HTTP 400 `invalid_payload`, as in the live check, and no call. 3 of 3 |
| The confidence question to that agent with no tool choice in the request | The agent read the item `unrelated` and then answered `100`, although the question told it to call no tool. Once. In two more follow-ups without `none`, one of them with `tool_choice: required` in the request, the agent made a call that the service recorded, and the project answered HTTP 400 `tool_user_error` |
| A `previous_response_id` the project does not know | HTTP 400 `invalid_payload`, "Invalid previous_response_id" |

Two more observations:

- **The code interpreter.** A prompt agent with only the code interpreter was told to run code that prints a product. With `tool_choice: none` the response held no `code_interpreter_call`, 2 of 2, and the product the agent stated was wrong both times. Without the tool choice it held one, and the product was right. There is no service to ask here: this rests on the items of the response.
- **The response of the Attempt was not changed.** Read again after eight follow-ups to it, its output was the same.

Why the other two ways that worked are not used is recorded in [ADR 0009](../adr/0009-prompt-agent-follow-up-rests-on-tool-choice-none.md).

#### Resources of this verification

Four resource groups were created for this verification, all in the subscription named `Sandbox`:

| Resource group | What it was for |
| --- | --- |
| `rg-are-53-d33f3da8` | The fixture that was kept for the work: the requests by hand and the first run of the live check. |
| `rg-are-53-81a2f2f7` | The fixture of the second run, which the check provisioned and tore down itself. That run took 53 minutes end to end. |
| `rg-are-53-cdbda532` | Never became a fixture. `az containerapp up` ended with `ERROR: Run failed` while the image of the to-do service was being built, and the provisioning tore the group down. The next provisioning, with nothing changed, succeeded. The cause is not known. |
| `rg-are-53-a4e60902` | The fixture of the third run, which the check provisioned and tore down itself. That run took 50 minutes end to end. |

Each was deleted with the fixture's `teardown`, and the soft-deleted Foundry accounts were purged. Afterwards a separate `az group exists` returned `false` for each of the four.

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
- More than one Attempt at a time. Foundry cloud evaluation as the entry point has its own target and its own [live observations](../foundry_cloud_evaluation/readme.md#observed-live). Every Attempt and every follow-up here was made from customer code. The follow-up after an Attempt that Foundry cloud evaluation invoked was tried later: see [the confidence rating](../foundry_cloud_evaluation/readme.md#the-confidence-rating) of that target.
- All twelve metrics in one sweep on this target were not part of these checks. They were run later, for the [coverage matrix](../coverage_matrix/readme.md), which records what each metric did on a prompt agent from customer code.
- For the confidence follow-up: an agent with a tool of another type than an OpenAPI tool or the code interpreter, such as an MCP tool, file search or web search. A tool that the platform runs without a tool call of the model, such as memory. A model other than `gpt-4.1-mini`. A response of the follow-up that holds a tool call, a tool choice other than `none` in it, or no `previous_response_id`. A follow-up that is streamed.
