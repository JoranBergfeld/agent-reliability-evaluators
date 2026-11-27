# Foundry hosted agent target

`FoundryHostedAgentTarget` lets the Evaluator sweep make its Attempts against a hosted agent in your Foundry project. It runs in your own process, from your code or your CI, under your identity. Scoring also happens in your process.

```python
from reliability_evaluators.agent_target.foundry_hosted import FoundryHostedAgentTarget
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import Task, run_sweep

target = FoundryHostedAgentTarget(
    project_endpoint="https://<account>.services.ai.azure.com/api/projects/<project>",
    agent_name="my-hosted-agent",
)


def item_is_closed(attempt, response):
    closed = my_test_service.item_is_closed(attempt.attempt_id)
    return TaskOutcome(success=closed, source="final state of the to-do test service")


scorecard = run_sweep(
    tasks=[Task(task_id="close", prompt="Close the item.", success_rule=item_is_closed, attempts=5)],
    agent_target=target,
    metrics=["outcome_consistency"],
)
```

`my_test_service` stands for your own code.

## Assumptions

These come from the specification and are not confirmed decisions.

- **Only hosted agents that serve the Responses protocol are supported.** A hosted agent that serves only the Invocations protocol is reported as unsupported and is not invoked (specification open point 26). Nothing is tried instead.

## What it needs

- Python 3.10 or later and the standard library. The target calls the project's REST API with `urllib`. It needs no Azure SDK and no other package, so copying `src/reliability_evaluators/` stays enough. The metric definitions in `evaluators/` and `evidence/` do not import the target.
- A bearer token for the resource `https://ai.azure.com`. The target sends no API key.
- Network access from where the sweep runs to the project endpoint.

### Authentication

By default the token comes from the Azure CLI: the target runs `az account get-access-token --resource https://ai.azure.com` and keeps the token until five minutes before it expires. So the sweep runs as whoever the Azure CLI is signed in as.

| Where | How the Azure CLI gets its identity |
| --- | --- |
| Your machine | `az login` |
| GitHub Actions | The `azure/login` action, for example with a federated credential, in a step before the sweep |
| Azure Pipelines | Run the sweep inside an `AzureCLI@2` task with a service connection |

Pass `credential=AzureCliCredential(subscription="<name or id>")` when the signed-in identity has several tenants and the token must be for the tenant of one subscription.

If you would rather not depend on the Azure CLI, pass any function that returns a token for `https://ai.azure.com`:

```python
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
target = FoundryHostedAgentTarget(
    project_endpoint=...,
    agent_name=...,
    credential=lambda: credential.get_token("https://ai.azure.com/.default").token,
)
```

`azure-identity` is then a dependency of your project, not of this source. The function is called before every request, so it should keep its token until it is about to expire.

The identity needs a role in the Foundry project that allows it to read and invoke the agent. In the live check it held Foundry User on the Foundry account and Foundry Project Manager on the project. Which of the two is needed was not isolated.

Not verified: the two CI rows of the table and the `azure-identity` example. The live check ran on a workstation, signed in with `az login` as a user.

## What the agent is sent

One Responses request per Attempt, not streamed:

```text
POST <project endpoint>/agents/<agent name>/endpoint/protocols/openai/responses?api-version=v1
{"input": "<the Task's prompt>", "stream": false,
 "metadata": {"attempt_id": "<Attempt identity>", "task_id": "<Task identity>"}}
```

The prompt is sent exactly as it is. The `metadata` identifies the Attempt on the platform. The response returns it, and it is recorded.

A hosted agent cannot be configured per Attempt. If your agent has to be told which test data an Attempt works on, pass `compose_input`, a function of the Attempt request that returns what the agent is sent instead of the prompt:

```python
target = FoundryHostedAgentTarget(
    project_endpoint=...,
    agent_name=...,
    compose_input=lambda request: f"Use list {my_test_service.list_for(request.attempt_id)}. {request.prompt}",
)
```

The agent then no longer receives the prompt exactly as it is, so the composed input is recorded on the Attempt as it was sent. Under a prompt variation the prompt is the exact text of one of your prompt variants. Whatever `compose_input` puts around it has to be the same under the baseline and under every variant, or the comparison no longer measures the variant alone.

The request is sent once. The target never repeats a request that may have reached the agent. Retries are the sweep's, declared with `ExecutionLimits(max_retries=...)`, and every retry stays in the evidence: see [execution limits](../sweep/execution-limits.md).

The target does not reset anything. Whether the agent or your service carries state from one Attempt to the next is for your prepare control to settle: see [controlled experiments](../sweep/controlled-experiments.md).

## What is recorded for each Attempt

Every Attempt in the scorecard has an `invocation` entry with what the platform returned for it:

| Field | Contents |
| --- | --- |
| `agent_target`, `protocol`, `api_version` | `foundry_hosted_agent`, `responses`, `v1` |
| `composed_input` | What the agent was sent, when `compose_input` composed it. `null` when the agent was sent the prompt as it is |
| `http_status` | The HTTP status of the answer |
| `request_ids` | The `x-request-id` and `apim-request-id` headers of the answer |
| `response_id` | The identifier of the response |
| `agent_session_id` | The identifier of the agent session the platform ran the request in |
| `agent` | The agent reference of the response: the agent's name and the version that answered |
| `metadata` | The metadata the response returned: the Attempt identity and Task identity it was sent |
| `status`, `error` | The status and the error of the response |
| `token_usage` | The `usage` of the response, as the platform reported it |
| `token_usage_missing` | Why there is no usage, when the response carried none. Missing usage is never recorded as zero |
| `tool_calls` | The tool calls the response exposed, in order: `call_id`, `name`, `arguments` and the `output` the tool returned |

An Attempt whose invocation failed keeps the same entry with whatever the platform returned, so its request identifiers and any token usage are not lost. An Attempt for which no answer came back at all has `"invocation": null`.

`tool_calls` are the `function_call` items of the response, each paired with its `function_call_output` by `call_id`. The arguments are decoded from the JSON text the protocol gives them as. The output is kept as the text the tool returned. `tool_calls` is `null` when the response has no list of output items, which is not the same as an empty list.

The token usage is recorded, not reported: a token usage report is a separate piece of work. The usage is also not given to the sweep as a resource measurement, because it has not been established that it covers every model call of an Attempt.

### Resource measurements

| Resource type | Unit | Measurement boundary |
| --- | --- | --- |
| Elapsed time | seconds | Wall clock from just before the Responses request is sent until its response has been read, measured by the target. Getting the token and looking the agent up are not included. This replaces the sweep's own timing of the whole invocation |
| Tool calls | calls | The `function_call` items in the response's output, counted by the target. A tool call the agent does not report there is not counted. Left out when the response exposed no output items |

### Action evidence

The trajectory metrics need a declared source of action evidence. To use the tool calls of the response as that source:

```python
from reliability_evaluators.agent_target.foundry_hosted import NATIVE_TOOL_CALLS, native_tool_calls
from reliability_evaluators.sweep import ActionEvidenceDeclaration

action_evidence = ActionEvidenceDeclaration(
    evidence_source=NATIVE_TOOL_CALLS,
    record=native_tool_calls,
    action_types={"read_todo": "read", "update_todo": "update"},
)
```

The platform says what each tool returned, not whether the call failed. So no action event from this source carries an `error`; a failed call shows only in its result. The tool calls of a response and the calls your service received can differ, and there is no default between them: you declare which one a metric is calculated from.

## The task outcome never comes from the response

A hosted agent can answer that it is done after one of its tools failed, and the platform still reports that response as `completed`. The target does not look at the answer to decide anything. Your task-success rule decides the task outcome, and it should read independent evidence, such as the state of your test service.

A response with status `completed` and no error makes the Attempt's execution status `completed`. That says the agent ran. It says nothing about the Task.

## When the invocation fails

Each of these is an execution status of the Attempt with the reason in `execution_error`. None is a task failure: an Evaluator leaves the Attempt out and counts it with a reason.

| What happened | Execution status | `execution_error` starts with |
| --- | --- | --- |
| The endpoint answered with an HTTP error | `failed` | `InvocationFailed: the hosted agent endpoint answered HTTP <status>:` and the body |
| The response has a status other than `completed`, such as `failed` or `incomplete` | `failed` | `InvocationFailed: the response <id> of the hosted agent ended with status` |
| The response has status `completed` and carries an error | `failed` | `InvocationFailed: the response <id> of the hosted agent has status 'completed' and carries an error:` |
| The answer was not a response | `failed` | `InvocationFailed: the hosted agent endpoint answered HTTP 200 with a body that is not a response:` |
| The project could not be reached | `failed` | `InvocationFailed: the Foundry project could not be reached:` |
| The agent could not be looked up, for example because the name is wrong | `failed` | `InvocationFailed: agent '<name>' could not be looked up in the Foundry project:` |
| No answer within the time limit | `timed_out` | `TimeoutError: the hosted agent did not answer within <limit> seconds` |
| The agent is not supported | `failed` | `UnsupportedAgent:` |
| No token could be obtained | `failed` | `RuntimeError: the Azure CLI gave no token for the Foundry project:` |

The time limit is the `attempt_timeout_seconds` you declare in the Task's `ExecutionLimits`. Without one it is the target's own `timeout_seconds`, 300 by default. After a timeout nothing is known about what the agent did or is still doing.

## Agents that are not supported

Before its first request, the target asks the project about the agent (`GET <project endpoint>/agents/<agent name>?api-version=v1`), once. It refuses:

- a hosted agent whose endpoint does not serve the `responses` protocol, such as an Invocations-protocol agent;
- an agent that is not a hosted agent, such as a prompt agent.

A refused agent is never sent a request. Every Attempt then has the execution status `failed` and says why:

```text
UnsupportedAgent: hosted agent 'my-agent' serves the protocols ['invocations'] and not the Responses protocol.
Only hosted agents that serve the Responses protocol are supported, so it was not invoked
```

To find out before any Attempt is made, ask for the agent configuration when you declare your experiment. It raises `UnsupportedAgent` for an agent the target refuses:

```python
experiment = Experiment(..., agent_configuration=target.agent_configuration(), ...)
```

`agent_configuration()` returns the project endpoint, the agent's name, its kind, the protocols its endpoint serves, its latest version and its version selection rules. The project does not report which model a hosted agent uses, so add that yourself if you want it recorded. The project returns the hosted agent's environment variables with its details, in plain text. The target does not copy them anywhere.

## No confidence rating

The target declares that it does not support an isolated follow-up. Whether a Foundry hosted agent can be asked a follow-up question in the conversation of a completed Attempt, without being able to act, has not been verified. Until it is, every Attempt made through this target has no confidence rating, with that reason, and calibration, strict confidence discrimination and the Brier-based score are unavailable. The agent is not asked, and a rating in its task response is not used.

## Limits

- One request per Attempt, not streamed. A streamed response is not read.
- Attempts are invoked from your process only. Foundry cloud evaluation as the entry point is not covered here.
- The target looks the agent up once. A change to the agent during a sweep is seen only in the `agent` each response reports.
- An agent that reports its tool calls in another form than `function_call` items has a tool-call count of zero and no tool calls recorded.

## Observed live

Observed on 2026-11-27 in Sweden Central, on the disposable fixture in [`live_fixture/`](../../live_fixture/README.md), from a workstation with Azure CLI 2.93.0, signed in as a user. This records what happened. No documentation was consulted for it, and none of it is a reliability result for any agent.

The interfaces as they worked on that date, both with `api-version=v1` on the project endpoint:

| Purpose | Request |
| --- | --- |
| Look the agent up | `GET /agents/{name}` |
| Invoke the hosted agent | `POST /agents/{name}/endpoint/protocols/openai/responses` |

The fixture's hosted agent runs `gpt-4.1-mini` (`2025-04-14`) through Agent Framework and declares the `responses` protocol, version `2.0.0`.

The live check (`tests/live/test_foundry_hosted_agent_live.py`) runs the sweep from this source against that agent: three Attempts of the fixture's Task with every tool working, and five in which the service's update tool answers 503 in Attempts 2 and 4. It ran three times: twice on one fixture that was kept for the work, and once self-contained, where the check provisioned its own fixture, ran and tore it down. The three runs were made before the Attempt recorded `composed_input`; what the agent was sent has not changed since. The results were the same each time:

- **All 24 Attempts completed.** The 18 with every tool working succeeded under the fixture's strict rule, and the 6 with the failing update failed it.
- **Outcome consistency was 0.52 in the scorecard and 0.52 calculated independently**, in each of the three runs. The independent value is calculated in the test from the service's own evidence of each Attempt: 3 of 3 successes gives 1, 3 of 5 gives 0.04, and the mean is 0.52.
- **A failed tool call did not fail the response.** In all six Attempts with the failing update the response status was `completed`. In five of them the agent called update again, once or twice more. The task outcome was failure, from the service's evidence.
- **Each Attempt was identifiable.** Every response had its own `id` and its own `agent_session_id`. The response returned the `metadata` it was sent. Both request identifier headers were present, and `x-request-id` held its value twice, separated by a comma.
- **Tool calls were exposed in order** as `function_call` and `function_call_output` items: five in each Attempt without a fault, and five to seven with the failing update. In every Attempt the tool calls of the response were the calls the service received, in the same order.
- **Token usage was reported on all 24 responses**: 10,880 to 11,572 total tokens without a fault and 11,586 to 21,670 with the failing update. Whether it covers every model call was not established.
- **The request took 21.0 to 35.0 seconds, and once 86.4.** The slow one was the sixth Attempt of the self-contained run and had nothing else unusual about it.
- **A hosted agent declared with the `invocations` protocol was refused**, in each run. The project reported `"protocols": ["invocations"]` for its endpoint, and the target did not invoke it. The check creates that agent itself in the fixture's project, with the fixture's Responses code, so only its declaration was tested. Sent by hand, outside the target, a Responses request to it was answered with HTTP 400 `bad_request`: "Endpoint-scoped responses require the 'responses' protocol to be declared".
- **Teardown.** Both resource groups created for this work, `rg-are-44-3f3d7d2b` and `rg-are-44-3fe5273d`, were deleted, and a separate `az group exists` returned `false` for each. The soft-deleted Foundry accounts were purged. The self-contained run took 58 minutes end to end. Its eight Attempts took under five of them, and tearing down about half an hour.

Also observed, by hand on the first fixture:

- The project returns the hosted agent's environment variables in the agent's details, including the fixture's synthetic service key, to an identity that can read the agent.
- A completed response had no `error` key at all, rather than `"error": null`. Its `response_id` held the same value as its `id`.
- The three responses whose headers were inspected each reported a cold session start (`x-ms-debug-latency-session-start-type: cold`).
- One request made in the first minutes after the first fixture was provisioned got no answer within 300 seconds. It was made by a throwaway script, not by the target, and which of its requests it was is not known. It did not happen again, also not on the first invocation after the second fixture was provisioned.

Not verified here: an agent that really speaks the Invocations protocol, a response with a status other than `completed`, an HTTP error from the agent endpoint, a hosted agent built with anything but Agent Framework, authentication in CI or with `azure-identity`, more than one Attempt at a time, the confidence follow-up, and Foundry cloud evaluation as the entry point. The failure cases are covered by the default test run against a fake project that answers with the response shapes above.
