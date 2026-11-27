# Foundry cloud evaluation as the entry point

`FoundryCloudEvaluationTarget` lets the Evaluator sweep invoke its Attempts through Foundry cloud evaluation instead of from your own code. Foundry sends the request to your agent, in an evaluation run of your own Foundry project, under your identity. It works for a prompt agent and for a hosted agent that serves the Responses protocol.

The sweep still runs in your process, from your code or your CI. It decides which Attempt is made when, invokes your customer controls around every Attempt, and groups and scores the evidence. Only the invocation of the agent moves into Foundry. The two entry points, customer code and Foundry cloud evaluation, are independent of the scoring path.

```python
from reliability_evaluators.agent_target.foundry_cloud_evaluation import FoundryCloudEvaluationTarget
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryProject
from reliability_evaluators.sweep import Task, run_sweep

target = FoundryCloudEvaluationTarget(
    project=FoundryProject("https://<account>.services.ai.azure.com/api/projects/<project>"),
    agent_name="my-agent",
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

To invoke the same agents from your own code instead, use the [Foundry prompt agent target](../foundry_prompt_agent/readme.md) or the [Foundry hosted agent target](../foundry_hosted_agent/readme.md). [What differs between the entry points](#what-differs-from-the-customer-code-entry-point) is listed below.

| Argument | Meaning | Default |
| --- | --- | --- |
| `project` | Your Foundry project, as a `FoundryProject`. | Required |
| `agent_name` | The name of the agent. | Required |
| `agent_version` | The version of the agent the run must invoke. | The project chooses. The version that answered is recorded on each Attempt. |
| `compose_input` | A function of the Attempt request that returns what the agent is sent instead of the prompt. | The prompt is sent exactly as it is. |
| `run_timeout_seconds` | How long the target waits for a run to end when no time limit is declared for the Attempt. | 900 |
| `poll_interval_seconds` | How often the target asks the project whether the run has ended. | 5 |
| `clock`, `sleep` | The clock the time limit is measured on, in seconds, and the function that waits. | `time.monotonic`, `time.sleep` |

## What it needs

- Python 3.10 or later and the standard library. The requests are sent by `FoundryProject`, the same class [Foundry-managed scoring](../managed_scoring/readme.md) uses: plain REST with `urllib` and a bearer token. No Azure SDK: see [ADR 0005](../adr/0005-foundry-targets-use-the-standard-library.md).
- A bearer token for the resource `https://ai.azure.com`. By default `FoundryProject` asks the Azure CLI, so the sweep runs as whoever the Azure CLI is signed in as. Pass `FoundryProject(endpoint, token=...)` to use your own function. What the [hosted agent target's documentation](../foundry_hosted_agent/readme.md#authentication) says about signing in from CI holds here.
- A role in the Foundry project that allows the identity to read the agent and to create evals and evaluation runs. In the live check it held Foundry User on the Foundry account and Foundry Project Manager on the project. Which of the two is needed was not isolated.
- Network access from where the sweep runs to the project endpoint. Your process does not call the agent for an Attempt. Foundry does. The one request your process sends to the agent itself is [the confidence follow-up](#the-confidence-rating) to a prompt agent, when a metric that uses confidence is selected.

## One run per Attempt

A cloud evaluation run takes rows of data and invokes the agent once for every row. The target makes **one run with one row for every Attempt**, and for every retry. It waits until that run has ended before it returns to the sweep. The reasons are in [ADR 0007](../adr/0007-a-cloud-evaluation-run-invokes-one-attempt.md). What it means for you:

- **Repetition and grouping are the sweep's.** Five Attempts are five runs. Nothing relies on Foundry to repeat an invocation, to order rows, to group results or to reset anything. A run's counts and usage describe one Attempt.
- **Your customer controls run around every Attempt**, exactly as from customer code: prepare and apply before the run is made, verify and clean up after it has ended. See [controlled experiments](../sweep/controlled-experiments.md).
- **Sequential by default.** The next run is made only after the one before has ended and the controls of its Attempt have run. With `max_concurrent_attempts` above 1, the Attempts of Tasks given without an experiment are several runs at once. That was not tried.
- **The starting-state policy is yours to declare and the sweep's to follow.** Foundry starts a new conversation for every run. That resets nothing outside the conversation: whatever the agent's tools changed stays changed. State is fresh only when you declare `starting_state="fresh"` and your prepare control makes it so. Under continuing state the Attempts are still separate runs, each starting from what the one before left.
- **Every Attempt pays the overhead of a run.** When observed, the target returned about 25 seconds later than the latency Foundry reported for the agent. A sweep through this entry point is slower than the same sweep from customer code: see [Observed live](#observed-live).

All runs of one target are made in one eval, named `Reliability Evaluators: Attempts of <agent name> through cloud evaluation`, which the target creates in your project on its first Attempt. The evals and runs stay in your project, under your project's own access and retention. The target deletes nothing.

## What Foundry is sent

Once per target, the first time an Attempt is made:

```text
GET  <project endpoint>/agents/<agent name>?api-version=v1

POST <project endpoint>/openai/v1/evals
{"name": "Reliability Evaluators: Attempts of <agent name> through cloud evaluation",
 "data_source_config": {"type": "custom", "include_sample_schema": true,
                        "item_schema": {"type": "object", "properties": {
                            "query": {"type": "string"}, "attempt_id": {"type": "string"}, "task_id": {"type": "string"}}}},
 "testing_criteria": [<the criterion below>]}
```

For every Attempt:

```text
POST <project endpoint>/openai/v1/evals/<eval id>/runs
{"name": "Attempt <Attempt identity>",
 "data_source": {
   "type": "azure_ai_target_completions",
   "source": {"type": "file_content", "content": [
     {"item": {"query": "<the Task's prompt>", "attempt_id": "<Attempt identity>", "task_id": "<Task identity>"}}]},
   "input_messages": {"type": "template", "template": [
     {"type": "message", "role": "user", "content": {"type": "input_text", "text": "{{item.query}}"}}]},
   "target": {"type": "azure_ai_agent", "name": "<agent name>", "version": "<agent version>"}}}

GET  <project endpoint>/openai/v1/evals/<eval id>/runs/<run id>          until the run has ended
GET  <project endpoint>/openai/v1/evals/<eval id>/runs/<run id>/output_items?limit=100
```

The prompt is sent exactly as it is, as the one user message. `version` is sent only when you pass `agent_version`. The Attempt identity and the Task identity travel as columns of the row. The output item returns them, and the target pairs the item with the Attempt by that identity, never by the order of rows.

Your agent cannot be configured per Attempt. As with the other targets, either name the test resource in the Task's prompt and let your controls bring that one resource into its starting state, or pass `compose_input`:

```python
target = FoundryCloudEvaluationTarget(
    project=...,
    agent_name=...,
    compose_input=lambda request: f"Use list {my_test_service.list_for(request.attempt_id)}. {request.prompt}",
)
```

The agent then no longer receives the prompt exactly as it is, so the composed input is recorded on the Attempt as it was sent. Under a prompt variation, whatever `compose_input` puts around the prompt has to be the same under the baseline and under every variant.

The run is made once. The target never makes a second run for an Attempt. Retries are the sweep's, declared with `ExecutionLimits(max_retries=...)`, and every retry is a run of its own that stays in the evidence: see [execution limits](../sweep/execution-limits.md).

### The criterion Foundry requires

Foundry does not invoke the agent without a testing criterion. Observed live: an eval without one can be created, but a run on it fails after a few seconds with `ArgumentInvalid: The evaluators field is a required input in the evaluation request body`, and the agent is not invoked.

The offering has no LLM judge, and none is attached. The target attaches the least criterion that was found to work:

```json
{"type": "string_check", "name": "required_by_the_platform_not_a_judgement",
 "input": "{{item.attempt_id}}", "reference": "{{item.attempt_id}}", "operation": "eq"}
```

It compares the identity of the Attempt with itself. It calls no model, needs nothing registered in your project, and never looks at what the agent did. It therefore always passes, also for an Attempt whose invocation failed.

**What Foundry labels with it is a platform diagnostic and nothing else.** The label, the pass flag and the score of the item, and the pass counts of the run, are recorded under `invocation.platform_diagnostics` of each Attempt, with a sentence that says what they are. They are not a task outcome, not an assessment by an Evaluator and not your [acceptance criterion](../acceptance_criteria/readme.md). Nothing reads them. Foundry itself counts every such run as passed. That says nothing about your agent.

## What is recorded for each Attempt

Every Attempt the target was asked to make has an `invocation` entry, however the invocation ended:

| Field | Contents |
| --- | --- |
| `agent_target`, `entry_point`, `api_version` | `foundry_cloud_evaluation`, `foundry_cloud_evaluation`, `v1` |
| `agent_kind` | The type of the agent as the project reports it: `prompt` or `hosted` |
| `composed_input` | What the agent was sent, when `compose_input` composed it. `null` when the agent was sent the prompt as it is |
| `eval_id`, `run_id`, `output_item_id` | The eval, the run of the Attempt and its output item |
| `run_status`, `status` | The status of the run and of the output item |
| `criterion` | The testing criterion the run carried |
| `agent` | The agent the output item names: its `id`, `name` and the `version` that answered |
| `response_id`, `conversation_id` | The response of the agent and the conversation Foundry made for the run |
| `model` | The model the output item names: the deployment for a prompt agent, an empty text for a hosted agent |
| `metadata` | The Attempt identity and Task identity the output item returned |
| `error` | The error of the item's sample. For a run without an item, the error of the run |
| `token_usage` | The `usage` of the item's sample, as the platform reported it: `prompt_tokens`, `completion_tokens`, `cached_tokens`, `total_tokens` |
| `token_usage_missing` | Why there is no usage, when there is none. Missing usage is never recorded as zero |
| `run_per_model_usage` | The run's own usage rows, as returned. They are not a total: see below |
| `latency_ms` | The latency of the target, as Foundry measured it |
| `tool_calls` | The tool calls the item exposed, in order: `call_id`, `type`, `name`, `arguments` and the `output` the tool returned |
| `platform_diagnostics` | Foundry's labels and counts, with what they mean: `result_counts` and `per_testing_criteria_results` of the run, and the `results` of the item |

What was not reached is `null`. An Attempt whose run was never made has the entry with its `entry_point` and nothing from a run.

The run's link to the Foundry portal is not recorded: it embeds your subscription identifier.

**Tool calls.** The output item lists them as messages. A call is a part of an assistant message whose type ends in `_call`: `openapi_call` for the OpenAPI tool of a prompt agent, `function_call` for a hosted agent. What it returned is the part of a tool message, of the type `<that type>_output`, with the same `tool_call_id`. The arguments arrive as an object. The output is kept as the platform gave it: text for the OpenAPI tool, and an object for a hosted agent whose tool returned JSON text. `tool_calls` is `null` when the item has no list of them, which is not the same as an empty list.

**Token usage.** `token_usage` is what Foundry reported for the invocation of the agent in this Attempt. It is recorded here as the platform reported it, and the scorecard reports its `total_tokens` from here: per Attempt, per Evaluator and for the sweep, see [token usage](../token_usage/readme.md). It is not given to the sweep as a resource measurement, because it has not been established that it covers every model call of an Attempt. The criterion calls no model, and no usage was reported for it.

`run_per_model_usage` holds the rows Foundry reports for the whole run. For a prompt agent there were two, one named after the model deployment and one named `azure_ai_agent_target`, **with the same numbers**: adding them up counts every token twice. For a hosted agent there was one. They are kept so that nothing the run exposed is lost. They must not be summed, and the token usage report does not read them.

What is absent, compared with what a run could be hoped to expose: usage for an Attempt whose target failed, usage for an Attempt that timed out, any usage per model call, and anything that says whether the reported usage is complete.

`invocation` is part of the attempt-level evidence, so it is in `evidence.json` and in the human-readable report, and a [recalculation](../sweep/late-and-corrected-outcomes.md) keeps it.

### Resource measurements

| Resource type | Unit | Measurement boundary |
| --- | --- | --- |
| Elapsed time | milliseconds | The latency of the target that Foundry reported for the Attempt's output item, measured by Foundry, as the number it reported. Making the run, waiting for it, the criterion and reading the result are not included. This replaces the sweep's own timing of the whole invocation |
| Tool calls | calls | The tool calls in the output item, counted by the target. A tool call the platform does not report there is not counted |

An Attempt whose invocation failed has neither. If Foundry reports no latency for an Attempt that completed, which was not observed, the Attempt has the sweep's own timing of the whole run instead, under the sweep's own measurement boundary. [Resource consistency](../resource_consistency/readme.md) leaves a resource type out for a Task whose Attempts were measured with more than one boundary, and says so.

### Action evidence

The trajectory metrics need a declared source of action evidence. To use the tool calls of the output item as that source:

```python
from reliability_evaluators.agent_target.foundry_cloud_evaluation import NATIVE_TOOL_CALLS, native_tool_calls
from reliability_evaluators.sweep import ActionEvidenceDeclaration

action_evidence = ActionEvidenceDeclaration(
    evidence_source=NATIVE_TOOL_CALLS,
    record=native_tool_calls,
    action_types={"read_todo": "read", "update_todo": "update"},
)
```

The platform says what each tool returned, not whether the call failed. So no action event from this source carries an `error`; a failed call shows only in its result. The tool calls of an output item and the calls your service received can differ, and there is no default between them: you declare which one a metric is calculated from. An Attempt whose invocation failed has no action evidence from this source, although the agent may have acted.

## The confidence rating

When a selected metric uses confidence, the sweep asks the agent for a [confidence rating](../brier_based_score/readme.md#how-the-confidence-rating-is-collected) after each completed Attempt, in a separate request in which the agent must not be able to act.

**A prompt agent is asked.** Foundry cloud evaluation invoked the Attempt, and its output item names the response of the agent. The follow-up is not a run. It is one request of your own process to the project's Responses route, the request the [prompt agent target](../foundry_prompt_agent/readme.md#the-confidence-rating) sends:

```text
POST <project endpoint>/openai/v1/responses
{"input": "<the confidence question>",
 "previous_response_id": "<the response the output item of the Attempt names>",
 "agent_reference": {"type": "agent_reference", "name": "<agent name>", "version": "<the version that item names>"},
 "tool_choice": "none",
 "metadata": {"attempt_id": "<Attempt identity>", "task_id": "<Task identity>", "request": "confidence"}}
```

The target reads from the answer which response it continued, which tool choice it ran under and which tools were called. Unless it continued the Attempt's response, under `none`, with no tool call, the answer is not returned and the Attempt has no rating. What the isolation rests on, and its limits, are those of the prompt agent target: see [what the isolation rests on](../foundry_prompt_agent/readme.md#what-the-isolation-rests-on), [ADR 0009](../adr/0009-prompt-agent-follow-up-rests-on-tool-choice-none.md) and [ADR 0011](../adr/0011-a-cloud-evaluation-attempt-of-a-prompt-agent-is-followed-up-from-the-customers-process.md).

So with a predictability metric selected, your process needs what the prompt agent target needs: access to the Responses route as an identity that may invoke the agent.

**A hosted agent is not asked.** Its own code decides whether it acts in a follow-up, whichever entry point invoked the Attempt. Every Attempt then has no rating, with that reason, and the three predictability metrics are unavailable: see [no confidence rating](../foundry_hosted_agent/readme.md#no-confidence-rating).

The follow-up is recorded on the Attempt as `confidence_invocation`: the `response_id` of the answer, the `previous_response_id` and the `tool_choice` the project reported, the `tool_calls` of the answer, its `token_usage` and the `input` that was sent. Its `entry_point` is `customer_code`, because your process sent it. The Attempt's own `invocation` keeps `foundry_cloud_evaluation`. The usage of a follow-up has the fields of a response, `input_tokens`, `output_tokens` and `total_tokens`, and the [token usage report](../token_usage/readme.md#confidence-follow-ups) counts its `total_tokens` with the Attempt.

`target.isolated_follow_up` says which of the two holds for your agent. The target learns the type of the agent from the project, on its first Attempt or when you ask for `agent_configuration()`. Before that it declares nothing supported.

## The task outcome never comes from the run

The target does not look at the agent's answer, and not at Foundry's labels, to decide anything. Your task-success rule decides the task outcome, and it should read independent evidence, such as the state of your test service.

An output item with status `completed`, no error and a response of the agent makes the Attempt's execution status `completed`. That says the agent ran. It says nothing about the Task.

## A completed run can carry a failed invocation

**The status of a run and of its output item do not show that the agent ran.** Observed live, for a prompt agent whose tool returned an HTTP error: the run was `completed`, the output item was `completed`, the criterion was labelled `pass`, the run counted one passed item and no errored one, and only the item's `sample.error` said `TARGET_INVOCATION_FAILED`.

The target reads `sample.error` of every item. An item that carries one is an **execution failure**: the Attempt has the execution status `failed`, the reason in `execution_error`, and no task outcome. An Evaluator leaves it out and counts it with a reason. It is never a task failure. The run and item statuses and Foundry's label are recorded beside it, so the contradiction stays visible.

An item with no error that names no response of the agent is treated the same way: nothing shows that the agent ran.

## When the invocation fails

Each of these is an execution status of the Attempt with the reason in `execution_error`. None is a task failure.

| What happened | Execution status | `execution_error` starts with |
| --- | --- | --- |
| The run and its item report completed, and the item carries a target error | `failed` | `InvocationFailed: the cloud evaluation run <id> and its output item report 'completed', and the item carries a target error:` |
| The run ended as `failed` or `canceled`, for example because the project does not have the agent or the pinned version | `failed` | `InvocationFailed: the cloud evaluation run <id> ended as` |
| The run returned no output item, or more than one | `failed` | `InvocationFailed: the cloud evaluation run <id> returned <n> output items for the one Attempt it was given` |
| The run returned an item for another Attempt | `failed` | `InvocationFailed: the cloud evaluation run <id> returned an output item for` |
| The item has a status other than `completed` | `failed` | `InvocationFailed: the output item of the cloud evaluation run <id> ended with status` |
| The item names no response of the agent | `failed` | `InvocationFailed: the output item of the cloud evaluation run <id> reports 'completed' and names no response of the agent` |
| The project refused or did not answer a request | `failed` | `InvocationFailed:` and which request it was, such as `the cloud evaluation run could not be made in the Foundry project:` |
| The project answered the making of the eval or of a run with something else | `failed` | `InvocationFailed: the Foundry project answered the making of` |
| The run had not ended at the time limit | `timed_out` | `RunTimedOut: the cloud evaluation run <id> had not ended within <limit> seconds` |
| The agent is not supported | `failed` | `UnsupportedCombination:` |

The time limit is the `attempt_timeout_seconds` you declare in the Task's `ExecutionLimits`. Without one it is the target's own `run_timeout_seconds`, 900 by default. **It covers the whole run**, from when the target is invoked for the Attempt: the run's overhead as well as the agent's answer, and on the first Attempt also looking the agent up and making the eval. A limit that just fits an invocation from customer code is too short here.

At the time limit the target asks the project to cancel the run, and records the run with the status the project answered. **Cancelling a run does not stop an agent that was already invoked.** Observed live: a run cancelled two seconds after it was made was reported as `canceled` with no output item, and the hosted agent still carried out all five operations of the Task on the service. Your clean-up control has to cope with that. The Attempt has no usage: the result of the run was not read.

When the project does not answer the making of a run, nothing is known about whether a run was made and the agent invoked. The Attempt is an execution failure with no run recorded, and the target does not make the run again.

## Combinations that are not supported

An unsupported combination is reported. Nothing is tried in its place.

- **A hosted agent that does not serve the Responses protocol**, such as an Invocations-protocol agent, and **an agent that is neither a prompt agent nor a hosted agent.** Before its first run the target asks the project about the agent, once. For such an agent it makes no eval and no run. Every Attempt then has the execution status `failed` and says why:

  ```text
  UnsupportedCombination: hosted agent 'my-agent' serves the protocols ['invocations'] and not the Responses protocol.
  Invoking such an agent through Foundry cloud evaluation has not been verified, so it was not invoked
  ```

  To find out before any Attempt is made, ask for the agent configuration when you declare your experiment. It raises `UnsupportedCombination` for an agent the target refuses:

  ```python
  experiment = Experiment(..., agent_configuration=target.agent_configuration(), ...)
  ```

  `agent_configuration()` returns the entry point, the project endpoint, the agent's name, its kind, the protocols its endpoint serves, the version you pinned and its latest version. The project returns a hosted agent's environment variables with its details, in plain text. The target does not copy them anywhere.

- **The confidence follow-up of a hosted agent.** For a hosted agent the target declares that it does not support an isolated follow-up, as the hosted agent target does. Every Attempt of a hosted agent has no confidence rating, with that reason, and [calibration](../calibration/readme.md), [strict confidence discrimination](../strict_confidence_discrimination/readme.md) and the [Brier-based score](../brier_based_score/readme.md) are unavailable. The agent is not asked, and a rating in its task response is not used. A prompt agent is asked: see [the confidence rating](#the-confidence-rating).

- **Fault robustness for a prompt agent whose tool returns an HTTP error.** As from customer code, such a fault fails the whole invocation, so no Attempt under the fault completes and [fault robustness](../fault_robustness/readme.md) is unavailable. It is not 0. For a hosted agent the Attempt completes and the metric is calculated.

## What differs from the customer-code entry point

The same Task, the same controls and the same task-success rule give the same kinds of evidence through either entry point: an execution status, a task outcome from your rule, the identifiers of the invocation, the token usage the platform reported, the ordered tool calls and the two resource measurements. These are the differences.

| | Customer code | Foundry cloud evaluation |
| --- | --- | --- |
| Who sends the request to the agent | Your process | Foundry, in an evaluation run of your project |
| Requests per Attempt | One | One run, asked for until it ends, then its output items |
| Identity of the Attempt on the platform | Sent as `metadata` of the request, returned by the response | A column of the run's one row, returned by the output item. The response gets no metadata |
| Identifiers | Response, request identifier headers, HTTP status. For a hosted agent the agent session | Eval, run, output item, response, conversation. No request identifier headers, no HTTP status, no agent session |
| Model | The deployment the response names and the model that served it, for a prompt agent | The deployment, for a prompt agent. Not the model that served it |
| Token usage fields | `input_tokens`, `output_tokens`, `total_tokens` with details | `prompt_tokens`, `completion_tokens`, `cached_tokens`, `total_tokens` |
| Tool-call arguments | Decoded by the target from JSON text | An object as given |
| Tool-call output | The text the tool returned | As the platform gave it. For the hosted agent an object, decoded by the platform from the JSON text the tool returned |
| Status of a tool call | Reported for a prompt agent | Not reported |
| Elapsed time | Wall clock of the request in seconds, measured by the target | The latency of the target in milliseconds, measured by Foundry |
| Conversation | None is made | Foundry makes one per run and names it |
| A failing tool of a prompt agent | The project answers HTTP 400 `tool_user_error`. Request identifiers only | The run and the item report completed. `sample.error` says `TARGET_INVOCATION_FAILED` and quotes the same `tool_user_error`. No response, no usage |
| A failing tool of a hosted agent | The response completes | The item completes, with no error |
| Time limit | Covers the request | Covers the whole run |
| After a time limit | Nothing is recorded of the invocation | The eval and the run are recorded. The run's cancellation is requested |
| Platform labels | None | A pass or fail label per item, kept as a platform diagnostic |
| Confidence follow-up to a prompt agent | A request of your process that continues the response of the Attempt | The same request, of your process, that continues the response the output item names. It is not a run |
| What stays in your project | Nothing the target made | One eval per target and one run per Attempt |

The two entry points were never compared on the same invocation. Do not read a difference in token usage or in elapsed time between them as a difference in what your agent used.

## Limits

- One run with one row per Attempt. Rows are never batched.
- A hosted agent has to serve the Responses protocol.
- The target looks the agent up once, and decides from its latest version what type of agent it is. A change to the agent during a sweep is seen only in the `agent` each output item names.
- The run's data is sent inline. A prompt that makes the request larger than the project accepts is refused by the project, and the Attempt is an execution failure.
- The eval and the runs are not removed.

## Observed live

Observed on 2026-11-27 in Sweden Central, on the disposable fixture in [`live_fixture/`](../../live_fixture/README.md), from a workstation with Azure CLI 2.93.0 and Python 3.15, signed in as a user. The fixture's prompt agent runs `gpt-4.1-mini` (`2025-04-14`) with one OpenAPI tool. Its hosted agent runs the same model through Agent Framework and declares the `responses` protocol. This records what happened. The request shapes were taken from the prototypes of 2026-11-23 and 2026-11-24 and tried. No documentation was consulted, and none of this is a reliability result for any agent. Synthetic data only.

The interface as it worked on that date, on the project endpoint:

| Purpose | Request |
| --- | --- |
| Look the agent up | `GET /agents/{name}?api-version=v1` |
| Create the eval | `POST /openai/v1/evals`, with no `api-version` and no preview header |
| Make a run | `POST /openai/v1/evals/{eval id}/runs` |
| Ask for a run | `GET /openai/v1/evals/{eval id}/runs/{run id}` |
| Cancel a run | `POST /openai/v1/evals/{eval id}/runs/{run id}/cancel` |
| Read the output items | `GET /openai/v1/evals/{eval id}/runs/{run id}/output_items?limit=100` |

### What a run needs to invoke the agent

Each was one run of the prompt agent, made by hand before the target existed.

- **No criterion.** The eval was created. The run on it failed three seconds later with `ArgumentInvalid: The evaluators field is a required input in the evaluation request body`. The service recorded no call: the agent was not invoked.
- **One `string_check` criterion that compares `{{item.attempt_id}}` with itself.** The run invoked the agent and completed. Its result had the score `1`, the label `pass`, `passed: true` and no threshold. This is the criterion the target attaches, under another name.
- **One custom code evaluator that returns 1.0 whatever it is given**, registered in the project with the preview header `Foundry-Features: Evaluations=V1Preview`. The run invoked the agent and completed. Foundry reported a duration of 39 seconds for it, against 24 to 29 for runs with the `string_check`. Its result had the label `pass` and a threshold of 0.5. It is not used: it needs an evaluator registered in your project and a preview feature, for nothing the `string_check` does not do.
- **Rows sent inline** as `file_content`, each wrapped in `item`, reached the agent through `{{item.query}}`. The prototypes had only used an uploaded file with an agent target.
- A run was returned as `queued`, and reported as `in_progress` from the first time it was asked for. The run echoed `max_concurrency: 1` and `target.version: null`, neither of which was sent.

### The live check

`tests/live/test_foundry_cloud_evaluation_live.py` runs one sweep per agent type from this source, with `FoundryCloudEvaluationTarget` as the agent target, scoring outcome consistency and fault robustness. The experiment declares continuing state, with three Attempts under the baseline and three under a fault on the update tool. The prompt names one state of the to-do service, and the agent is asked to do the whole Task in every Attempt. It also checks that a hosted agent declared with the Invocations protocol is refused.

It ran twice on one fixture and passed both times, 13 checks in 9 minutes 29 seconds and in 9 minutes 10 seconds. The second run was made with the target and the check as they are here, apart from the wrapping of long lines. The first differed in one thing: it recorded the elapsed time in seconds, derived from the latency. The results were the same in kind both times. That is 24 Attempts, each a cloud evaluation run of its own: 12 per agent type. The token usage report and changes to Foundry-managed scoring were merged into the sweep after that. The check does not use them and was not run again.

- **Every Attempt recorded its entry point** as `foundry_cloud_evaluation`, and the type of its agent. Each sweep made one eval and six runs, and every run counted one item.
- **Outcome consistency was 0.111 in the scorecard and 0.111 calculated independently**, for both agent types, both times. The check decides each task outcome itself, from the events and the final state the service had recorded when the Attempt ended, which the verify control reads. The first baseline Attempt left the service with exactly the five operations of the Task and succeeded. Each later one added five more and failed the strict task-success rule. p = 1/3, (2 × 1/3 − 1)² = 1/9.
- **A new run and a new conversation did not reset the service.** Every baseline Attempt had a conversation identifier of its own, and the service held 5, 10 and 15 operations after the three. Had each run been a reset, every Attempt would have succeeded and the value would have been 1.
- **Each completed Attempt was identifiable.** Every output item named its own response (`resp_…` for the prompt agent, `caresp_…` for the hosted agent) and its own conversation, the agent with its version `1`, and returned the Attempt identity and the Task identity of its row. The item named the model deployment for the prompt agent and an empty text for the hosted agent.
- **Tool calls were exposed in order**, five in each completed Attempt: `openapi_call` parts for the prompt agent and `function_call` parts for the hosted agent. In every one the tool calls of the item were the calls the service received during the Attempt, in the same order.
- **Token usage was reported on every item whose target completed**: 806 to 849 total tokens for the prompt agent and 10,563 to 10,605 for the hosted agent. Whether it covers every model call was not established. For the prompt agent the run had two usage rows with the same numbers, each equal to the usage of its one item. For the hosted agent it had one.
- **Foundry reported a latency of 5.5 to 7.5 seconds for the prompt agent and 28.2 to 31.3 seconds for the hosted agent.**
- **Foundry labelled every one of the 24 items as passed**, with a score of 1 and no threshold, and every run counted one passed item and no errored one. That includes the six prompt-agent Attempts whose target failed.
- **Prompt agent under the fault: completed on the platform, failed in the scorecard.** In all six, the run and the output item reported `completed` and the item's sample error said `TARGET_INVOCATION_FAILED`, quoting `Error code: 400` and `tool_user_error`. Each was recorded as `failed`, with no task outcome. The item had no response, no conversation, no agent version, no usage, no latency and an empty list of tool calls, and the run had no usage row. During each, the service recorded four successful calls and then the update, answered with 503 by the injected fault. The verify control observed the fault from those events. **Fault robustness was unavailable**, with the reason `no valid Attempt under the fault variation`.
- **Hosted agent under the fault: completed, with a task outcome of failure.** In all six, the item carried no error and the Attempt completed. The tool calls of the item showed the update answered with `{"status": 503, "body": {"error": "injected fault", "tool": "update"}}`. The agent called update once and then answered. The task outcome was failure, from the service's evidence. **Fault robustness was 0**: accuracy 0 under the fault against 1/3 under the baseline.
- **A hosted agent declared with the `invocations` protocol was refused** by the target, both times, with `UnsupportedCombination`. The Attempt recorded its entry point, and no eval and no run were made for it. The check creates that agent itself in the fixture's project, with the fixture's Responses code, so only its declaration was tested.

### Single invocations through the target, outside the check

- **Pinned version.** With `agent_version="1"`, version `1` answered. With a version that does not exist, the run was made and ended as `failed` with `EvalValidationFailed` and the detail `ResourceNotFound: The AzureAIAgentTarget 'todo-prompt' was not found. Verify the name and version are correct.`, eleven seconds after the target was invoked. The service recorded no call. The fixture's agents have one version each, so pinning one of several was not tried.
- **Composed input.** With a prompt that names no state and a `compose_input` that puts one in front of it, the agent worked on that state, the Task succeeded, and the Attempt recorded the input as it was sent.
- **An agent the project does not have** was answered with HTTP 404 `not_found` when the target looked it up. No eval and no run were made.
- **Time limit.** With a limit of 12 seconds declared for the Attempt, the target raised `RunTimedOut` after 13.6 seconds for the hosted agent and 13.8 for the prompt agent, with the run recorded as `canceled`. The service had the five operations of the Task for both, 75 and 45 seconds later: the agent had been invoked, and the cancellation did not stop it.
- **What a run costs in time.** Two invocations of the prompt agent took 31.9 and 35.1 seconds from invoking the target until it returned, with a reported latency of 7.4 and 9.8 seconds. The target asks for the run every five seconds.

### Single runs, made by hand

- **Cancellation.** A run of the hosted agent was cancelled two seconds after it was made. The cancellation was answered with the run as `canceled`, and the run had no output items. A minute later the service had recorded all five operations of the Task.
- **What a run reports about itself.** Foundry reported a duration of 24 to 29 seconds for three runs of the prompt agent with the `string_check` criterion, two of whose items had a latency, of 6.2 and 6.7 seconds. For two runs of the hosted agent it reported 51 and 53 seconds, with a latency of 29 and 30 seconds. The run with the code evaluator reported 39 seconds.
- **An Invocations-declared hosted agent, not through the target.** A run that named the agent the check creates was not refused by Foundry. Foundry called the agent's `…/endpoint/protocols/invocations` route itself, and 87 seconds after it was made the run completed with an item that carried `TARGET_INVOCATION_FAILED`: HTTP 424 `session_not_ready`. The criterion was labelled `pass`. That agent runs Responses code under an Invocations declaration, so this shows that Foundry chooses the protocol from the declaration, and nothing about an agent that really speaks the Invocations protocol.
- The run's `report_url` named the resource group, the Foundry account and the project, after an encoded identifier.
- An eval and a run record the display name of the identity that created them, as `created_by`. The target does not copy it.

### Resources

One resource group was created for this work, in the subscription named `Sandbox`: `rg-are-45-f6638d25`, provisioned with `python -m live_fixture provision --ticket 45`. It held the fixture for everything above. It was deleted with the fixture's `teardown`, which took 29 minutes, and the soft-deleted Foundry account was purged. Afterwards a separate `az group exists` returned `false`, and no soft-deleted account of the fixture was listed. The evals, the runs and the code evaluator made above were in that project and went with it.

### Not verified

- The self-contained form of the live check, in which it provisions and tears down its own fixture. The check was run on a fixture provisioned beforehand.
- More than one Attempt at a time, and so several runs at once.
- A run with more than one row. The target never makes one.
- A follow-up that continues the conversation the output item names, in place of the response, and a follow-up made through a run. Neither was tried.
- The follow-up of a prompt agent whose definition specifies a `tool_choice` of its own, after a cloud evaluation Attempt. From customer code the project refused it.
- An agent that really speaks the Invocations protocol, and an agent that is neither a prompt agent nor a hosted agent.
- A run that fails after it invoked the agent, an output item with a status other than `completed`, a run that returns no item or the item of another row, a project that refuses to make a run, and an answer that is no run. The default test run covers them against a fake project that answers in the shapes above.
- A target error other than the one a failing tool of a prompt agent gives. The prototypes saw `(SystemError) Agent response '…' ended with status 'failed'` for a hosted agent at low model capacity on 2026-11-24. It was not seen here.
- Whether the reported token usage covers every model call, and what a run reports for an Attempt that timed out: its result is not read.
- A criterion other than the two above, an uploaded file or a dataset as the source of the rows, and a prompt near the size limit of a request.
- Authentication in CI or with `azure-identity`, and which roles are really required.
- What the Foundry portal shows for these evals and runs. None was opened there.
