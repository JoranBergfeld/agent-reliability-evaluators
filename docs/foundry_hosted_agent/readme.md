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
| `entry_point` | `customer_code`: the request was sent from your process. The [cloud evaluation target](../foundry_cloud_evaluation/readme.md) records `foundry_cloud_evaluation` |
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
| `token_usage_source` | Where the usage comes from, when there is one: the response the hosted agent returned, which the agent's own code produces. The usage report repeats it as the `source` of the count |
| `tool_calls` | The tool calls the response exposed, in order: `call_id`, `name`, `arguments` and the `output` the tool returned |

An Attempt whose invocation failed keeps the same entry with whatever the platform returned, so its request identifiers and any token usage are not lost. An Attempt for which no answer came back at all has `"invocation": null`.

`tool_calls` are the `function_call` items of the response, each paired with its `function_call_output` by `call_id`. The arguments are decoded from the JSON text the protocol gives them as. The output is kept as the text the tool returned. `tool_calls` is `null` when the response has no list of output items, which is not the same as an empty list.

The token usage is recorded here as the platform reported it, and the scorecard reports it from here: per Attempt, per Evaluator and for the sweep, see [token usage](../token_usage/readme.md). The usage is not given to the sweep as a resource measurement.

**The usage of a hosted agent is the agent's own count.** A hosted agent's own code produces its response, usage included, and Foundry passes it on. [Observed live](../token_usage/readme.md#observed-live), the usage the fixture's agent stated was not what the model deployment metered: equal with one model call, and 10,527 tokens stated against 3,358 metered for the six model calls of the fixture's Task. The numbers fit a count that adds to every model call the tokens of the calls before it. The target records the usage as the response stated it and says in `token_usage_source` whose count it is. Nothing corrects it. Compare it with your own deployment's metrics before you read it as tokens used.

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

The target declares that it does not support an isolated follow-up. The agent is not asked for its confidence. Every Attempt made through this target has no confidence rating, with this reason, and calibration, strict confidence discrimination and the Brier-based score are unavailable:

```text
the agent target does not support an isolated follow-up: the code of a Foundry hosted agent decides what it does
with a request, so a caller cannot ensure that the agent does not act while it answers a follow-up
```

A rating that the agent wrote in its task response is not used. There is no fallback.

Isolated means that the agent answers in the conversation of its Attempt, and that it cannot act while it answers. For a hosted agent the first can be had. The second cannot be ensured by a caller.

- **The conversation can be continued.** A request that named the Attempt's response in `previous_response_id` was answered with that conversation. Observed live, and Microsoft documents it for the Responses protocol.
- **Nothing a caller sends decides whether the agent acts.** A hosted agent is your code in a container. The project hands the request to that code, and the code decides whether a tool is called. Observed live: the fixture's agent and a copy of it with one line of its own code changed were sent the same follow-up, with `tool_choice: none` and an instruction to create an item. The fixture's agent called nothing. The copy created the item. A caller cannot check afterwards either: the response of a hosted agent did not say which tool choice it ran under.

This says nothing against your agent. The fixture's agent did honour the tool choice. But the target invokes any hosted agent, and from outside it cannot tell one that honours a tool choice from one that does not. So it declares the follow-up unsupported for all of them. The [Foundry prompt agent target](../foundry_prompt_agent/readme.md#the-confidence-rating) differs: for a prompt agent the project applies the tool choice itself.

If you own the code of your hosted agent and can vouch that it cannot act in a follow-up, an agent target of your own can declare the isolated follow-up: see [running it through an Evaluator sweep](../brier_based_score/readme.md#running-it-through-an-evaluator-sweep). That declaration is then yours to stand behind.

What was observed is under [the confidence follow-up](#the-confidence-follow-up).

## Limits

- One request per Attempt, not streamed. A streamed response is not read.
- This target invokes the agent from your process only. To invoke it through Foundry cloud evaluation, use the [cloud evaluation target](../foundry_cloud_evaluation/readme.md), which also lists what differs between the two entry points.
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

The live check (`tests/live/test_foundry_hosted_agent_live.py`) runs the sweep from this source against that agent: three Attempts of the fixture's Task with every tool working, and five in which the service's update tool answers 503 in Attempts 2 and 4. It ran three times: twice on one fixture that was kept for the work, and once self-contained, where the check provisioned its own fixture, ran and tore it down. The three runs were made before the Attempt recorded `composed_input` and `entry_point`; what the agent was sent has not changed since. The results were the same each time:

- **All 24 Attempts completed.** The 18 with every tool working succeeded under the fixture's strict rule, and the 6 with the failing update failed it.
- **Outcome consistency was 0.52 in the scorecard and 0.52 calculated independently**, in each of the three runs. The independent value is calculated in the test from the service's own evidence of each Attempt: 3 of 3 successes gives 1, 3 of 5 gives 0.04, and the mean is 0.52.
- **A failed tool call did not fail the response.** In all six Attempts with the failing update the response status was `completed`. In five of them the agent called update again, once or twice more. The task outcome was failure, from the service's evidence.
- **Each Attempt was identifiable.** Every response had its own `id` and its own `agent_session_id`. The response returned the `metadata` it was sent. Both request identifier headers were present, and `x-request-id` held its value twice, separated by a comma.
- **Tool calls were exposed in order** as `function_call` and `function_call_output` items: five in each Attempt without a fault, and five to seven with the failing update. In every Attempt the tool calls of the response were the calls the service received, in the same order.
- **Token usage was reported on all 24 responses**: 10,880 to 11,572 total tokens without a fault and 11,586 to 21,670 with the failing update. Whether it covers every model call was not established here. Compared with the deployment's own metrics later, it was about three times what was metered: see [token usage](../token_usage/readme.md#observed-live).
- **The request took 21.0 to 35.0 seconds, and once 86.4.** The slow one was the sixth Attempt of the self-contained run and had nothing else unusual about it.
- **A hosted agent declared with the `invocations` protocol was refused**, in each run. The project reported `"protocols": ["invocations"]` for its endpoint, and the target did not invoke it. The check creates that agent itself in the fixture's project, with the fixture's Responses code, so only its declaration was tested. Sent by hand, outside the target, a Responses request to it was answered with HTTP 400 `bad_request`: "Endpoint-scoped responses require the 'responses' protocol to be declared".
- **Teardown.** Both resource groups created for this work, `rg-are-44-3f3d7d2b` and `rg-are-44-3fe5273d`, were deleted, and a separate `az group exists` returned `false` for each. The soft-deleted Foundry accounts were purged. The self-contained run took 58 minutes end to end. Its eight Attempts took under five of them, and tearing down about half an hour.

Also observed, by hand on the first fixture:

- The project returns the hosted agent's environment variables in the agent's details, including the fixture's synthetic service key, to an identity that can read the agent.
- A completed response had no `error` key at all, rather than `"error": null`. Its `response_id` held the same value as its `id`.
- The three responses whose headers were inspected each reported a cold session start (`x-ms-debug-latency-session-start-type: cold`).
- One request made in the first minutes after the first fixture was provisioned got no answer within 300 seconds. It was made by a throwaway script, not by the target, and which of its requests it was is not known. It did not happen again, also not on the first invocation after the second fixture was provisioned.

All twelve metrics in one sweep on this target were not part of this check. They were run later, for the [coverage matrix](../coverage_matrix/readme.md), which records what each metric did on a hosted agent from customer code.

Not verified here: an agent that really speaks the Invocations protocol, a response with a status other than `completed`, an HTTP error from the agent endpoint, a hosted agent built with anything but Agent Framework, authentication in CI or with `azure-identity`, more than one Attempt at a time, and a conversation object in the place of `previous_response_id`. Foundry cloud evaluation as the entry point has its own target and its own [live observations](../foundry_cloud_evaluation/readme.md#observed-live). Nothing here says whether a confidence follow-up can be made when Foundry cloud evaluation makes the Attempts. The failure cases are covered by the default test run against a fake project that answers with the response shapes above.

### The confidence follow-up

Observed on 2026-11-27 in Sweden Central, on fixtures of the same kind. The requests went to `POST /agents/{name}/endpoint/protocols/openai/responses?api-version=v1`. The fixture's hosted agent runs `gpt-4.1-mini` (`2025-04-14`) through `agent-framework` 1.26.0, `agent-framework-foundry` 1.16.2 and `agent-framework-foundry-hosting` 1.0.0b261112. This records what happened. It is not a reliability result for any agent. Synthetic data only.

Whether an agent acted was read from the to-do service: the calls it received, before and after each follow-up.

`tests/live/test_confidence_follow_up_live.py` ran three times, each time on a fixture of its own: once on a fixture that was kept for the work, and twice self-contained. The first two runs were made before token usage reporting was merged into the sweep, and the third after it. The cloud evaluation target was merged after the third run. Neither merge changed what this target does or what the check sends to a hosted agent. Every run passed, with the same findings:

- **Through the sweep, the agent was not asked.** Two Attempts in each run completed. The agent had been asked, in the composed input, to end its task response with a rating, and each of the six responses ended with a line such as `CONFIDENCE=100`. No Attempt had a rating: each recorded the target's reason, and no confidence request was sent. Calibration, strict confidence discrimination and the Brier-based score were unavailable with the reason `no valid confidence/outcome pair`.
- **A follow-up that names the Attempt's response is answered with that conversation.** Sent by the check itself, not through the target: asked which identifier it had been told to use and which title the tool had returned, the agent answered with both, for both agents in every run.
- **The separate confidence-only turn was answered.** Sent the confidence question with `tool_choice: none`, the fixture's agent answered `100` and the service received no call, in each of the three runs. That shows that no call occurred in those turns. It does not show that none can.
- **Whether the agent acts is decided by its own code.** The check deploys the fixture's agent a second time with one line changed: `ResponsesHostServer(agent, prepare_options=lambda request, options: {})`, which makes the hosting package drop the options a caller sends. Both agents were then sent the same follow-up: an instruction to create an item, with `tool_choice: none`. The fixture's agent called nothing, 3 of 3. The copy called its create tool and the service recorded the new item, 3 of 3.
- **The response does not say which tool choice applied.** The response to the confidence-only turn had no `tool_choice`, for either agent in any run.

By hand on the first fixture of this verification, with a throwaway script, the fixture's agent:

| What was sent | What happened |
| --- | --- |
| A question about the Attempt, with the Attempt's response as `previous_response_id` | Answered with the identifier and the title |
| The same question with no `previous_response_id` | The agent answered that it had been told no identifier |
| The same question with no `previous_response_id` and the `agent_session_id` of the Attempt | The same: the session alone did not carry the conversation |
| An instruction to create an item, in the conversation, no tool choice | The agent created the item. Once |
| The same instruction with `tool_choice: none` | Twenty messages in which the agent wrote that it was creating the item, and no call. Once |
| The same instruction with `"tools": []` | The agent created the item. Once |

By hand, the copy that drops the caller's options created the item under `tool_choice: none` 2 of 2 times.

Read in the source of `agent-framework-foundry-hosting` 1.0.0b261112, and not run apart from the above: the package passes the fields of a request that it does not control itself, `tool_choice` among them, to the agent as options of the run. It offers the developer of an agent `prepare_options`, described there as a "developer hook to remove or replace caller model options". For an agent that does not accept run options it ignores them, with a warning by default.

Documented by Microsoft, read on 2026-11-27, and not our observation: a hosted agent is a container that implements the protocol, to which the platform sends each request ([hosted agent runtime contract](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agent-contract)). For the Responses protocol, "Conversation continuity comes from `previous_response_id` or a `conversation` ID—not from the session ID" ([manage hosted agent sessions](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/manage-hosted-sessions)). No documentation was found of a way for a caller to stop a hosted agent from calling a tool.

The resource groups of this verification are listed with the [prompt agent target](../foundry_prompt_agent/readme.md#resources-of-this-verification).
