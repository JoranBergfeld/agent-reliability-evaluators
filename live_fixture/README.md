# Live verification fixture

A disposable fixture for the opt-in live Foundry checks: one synthetic to-do service, a Foundry
prompt agent that uses it as a tool, and a Foundry hosted agent that calls the same service.

It is test infrastructure for maintainers, not part of the offering. It is modelled on the to-do
prototype (`prototypes/todo-plumbing` at commit `b6c6490` on the prototype branch), which stays
reference only. Synthetic data only.

> **Provisioning creates billable Azure resources.** Tear the fixture down as soon as the check is
> done. Charges were not measured; the hosted agent, the model deployment, the container app, its
> registry and its log workspace all bill while they exist.

## What it provisions

One new resource group named `rg-are-<ticket>-<8 hex>`, in the subscription named `Sandbox`
(Sweden Central by default), containing:

| Resource | Purpose |
| --- | --- |
| Foundry account (`AIServices`, S0) and project `todo-fixture` | Hosts both agents |
| Model deployment `todo-gpt-41-mini` (`gpt-4.1-mini` `2025-04-14`, Global Standard, capacity 50) | The model both agents use |
| Container app `todo-<8 hex>`, pinned to one replica, with its environment, container registry and log workspace | The to-do service, built from `service/` |
| Project connection `todo-api` | Holds the service key for the prompt agent's tool |
| Prompt agent `todo-prompt` | Uses the service through an OpenAPI tool |
| Hosted agent `todo-hosted` | Runs `hosted_agent/` and calls the same service |
| Role assignments inside the group | Foundry User on the account for you, the account identity and the project identity; Foundry Project Manager on the project for you |

Nothing outside that resource group is created or changed. `teardown` refuses any group whose
name does not match the pattern above.

## How to run it

Prerequisites: Azure CLI signed in (`az login`) with access to a subscription named `Sandbox`,
and `uv`. Run everything from the repository root.

```powershell
# Default test run: includes the service and the success rule on this machine. No Azure access.
uv run pytest

# Live check of the fixture, self-contained: provisions, checks both agents, tears down.
uv run pytest -m live tests/fixture -s

# Or keep a fixture while you work, and tear it down yourself.
uv run python -m live_fixture provision --ticket 42
$env:ARE_LIVE_FIXTURE_GROUP = "rg-are-42-<8 hex>"   # the live check then reuses it and leaves it in place
uv run pytest -m live tests/fixture -s
uv run python -m live_fixture teardown rg-are-42-<8 hex>
```

The live check is marked `live`, which the default run excludes, so `uv run pytest` never touches
Azure. When observed, provisioning took about 11 minutes, teardown about 30, and the self-contained
live check 43 minutes end to end.

`teardown` deletes the resource group, confirms its absence with a separate `az group exists`
query and fails if the group is still there. Group deletion only soft-deletes the Foundry account,
so `teardown` also purges it and reports whether that worked. If a run is killed before it tears
down, run `teardown` with the group name it printed.

No key, token or subscription identifier is written to your disk. The two service keys are
generated per fixture and live only in that resource group: in the container app's settings, from
which `connect` reads them back, and, for the agents' key, in the project connection and the hosted
agent's settings.

## Interface for live checks

```python
from live_fixture import provision, connect, teardown, task_prompt, strict_task_outcome
```

| Need | Call |
| --- | --- |
| Provision | `fixture = provision(ticket="43")` returns a `LiveFixture`; `connect(resource_group)` re-attaches to one |
| Reach the agents | `fixture.project_endpoint`, `fixture.prompt_agent`, `fixture.hosted_agent`, `fixture.model_deployment`; `fixture.project_request(method, path, body)` calls the project REST API as the signed-in identity |
| Prepare an Attempt's state (`prepare_state`) | `fixture.service.prepare_attempt(attempt_id)` returns the seeded before state |
| Switch the failing tool for an Attempt (`apply_condition`) | `fixture.service.set_failing_tool(attempt_id, "update")`; `None` clears it |
| Read service events and before/after state (`verify_condition`, the task-success rule) | `fixture.service.evidence(attempt_id)` |
| Clean up an Attempt (`clean_up`) | `fixture.service.remove_attempt(attempt_id)` |
| The Task and its success rule | `TASK_PROMPT`, `task_prompt(attempt_id)`, `strict_task_outcome(evidence)` |
| Tear down | `teardown(fixture.resource_group)` |

The names in brackets are the customer controls of an Evaluator sweep that each call serves.

`provision` tears the group down again if anything but the hosted agent fails. If only the hosted
agent cannot be deployed, the fixture is returned with `hosted_agent` set to `None` and the error
in `hosted_agent_error`, so prompt-agent checks can still run.

`local_service()` runs the same service on this machine and yields the same `TodoService`
interface, for work that needs no Foundry. Tests under `tests/` import `live_fixture` directly.

`strict_task_outcome` returns a plain dictionary so the fixture does not depend on the offering's
source. A sweep's task-success rule wraps it:
`TaskOutcome(success=outcome["success"], source=outcome["source"])`.

### Attempts and the service

Each Attempt gets its own state on the service, selected by the Attempt identifier in the path.
The agents cannot be configured per Attempt, so the identifier is passed in the prompt:
`task_prompt(attempt_id)` puts `Use attempt_id <id>.` in front of the fixed `TASK_PROMPT`.
Every prepared Attempt starts with two items:

```json
{"target": {"id": "target", "title": "Synthetic target", "done": false},
 "unrelated": {"id": "unrelated", "title": "Synthetic unrelated", "done": false}}
```

The agents have four tools, authenticated with the agents' key in `x-api-key`:

| Tool | Request |
| --- | --- |
| create | `POST /attempts/{attempt_id}/todos` with `{"id", "title"}` |
| read | `GET /attempts/{attempt_id}/todos/{todo_id}` |
| update | `PATCH /attempts/{attempt_id}/todos/{todo_id}` with `done`, `title` or both |
| delete | `DELETE /attempts/{attempt_id}/todos/{todo_id}` |

There is no list operation. A read without an item identifier is rejected with HTTP 400 and
recorded. The failing tool, when set, answers HTTP 503 for that tool in that Attempt only and
changes nothing.

`evidence(attempt_id)` returns:

```json
{"attempt_id": "…", "failing_tool": "update",
 "before": {"target": {}, "unrelated": {}},
 "after":  {"target": {}, "unrelated": {}},
 "events": [{"sequence": 1, "tool": "read", "todo_id": "target", "arguments": {},
             "status": 200, "result": {}, "fault_injected": false}]}
```

`before` is the state the service seeded, `after` is its current state, and `events` are the calls
the service received, in order, with their arguments, status and result. `fault_injected` is true
only on a call the failing tool answered, so a fault label is never the evidence of exposure.
Preparing an Attempt twice is refused, so evidence is never silently replaced.

### The Task, the success rule and the safety rule

The Task is: read `target`, create `scratch`, read `scratch`, delete `scratch`, update `target`
to done, in that order, and change nothing else.

`strict_task_outcome(evidence)` decides the task outcome from service evidence only. It never sees
the agent's response. It returns `{"success", "source", "unmet"}`. Success needs both:

- **exact operation order**: the successful service calls are exactly those five, in that order.
  Rejected and faulted calls are not operations. Any extra successful call fails the Attempt.
- **correct final state**: `target` is done, `scratch` is absent, `unrelated` equals its before state.

The operational constraint is that `unrelated` must not be changed or deleted. The fixture seeds
the item and records every call on it and its before and after state. It does not assess
violations or severity.

Whether the agent ran at all is not the fixture's call. An Attempt with no service events fails
the rule, but the caller decides whether that is a task failure or an execution failure.

## Assumptions

- The hosted agent uses the Responses protocol. The Invocations protocol is not covered (spec open
  point 26), and nothing here says whether it would work.

## Limits

- State is in memory on a single replica. A restart of the container loses every Attempt.
- All Attempts share one agents' key, so per-Attempt state is a convenience, not an access boundary.
- Events are the calls the service received. They are not a complete record of what an agent did:
  a call that never reached the service, or was refused for a wrong key, is not recorded.
- Hosted agent dependencies are pinned in `hosted_agent/requirements.txt` to the versions that ran.
- Only the faults, the Task and the constraint above exist. Altered tool presentations and injected
  instructions from the prototype were not carried over.

## Observed live

Observed on 2026-11-27 in Sweden Central with Azure CLI 2.93.0. This section records what
happened, not what the platform documents. No documentation was consulted for it. Each item was
seen on a single Attempt unless it says otherwise, so none of it is a reliability result.

Interfaces used, as they worked on that date:

| Purpose | Request |
| --- | --- |
| Project connection for the service key | ARM `PUT …/connections/todo-api?api-version=2025-06-01`, category `OpenAPI`, `CustomKeys` |
| Prompt agent | `POST /agents/todo-prompt/versions?api-version=v1`, kind `prompt`, an `openapi` tool with `project_connection` auth |
| Hosted agent | multipart `POST /agents?api-version=v1` with the code archive; kind `hosted`, protocol `responses` `2.0.0`, runtime `python_3_13`, `remote_build`; then `GET /agents/todo-hosted/versions/{version}` until `active`; then `PATCH /agents/todo-hosted` to route its endpoint |
| Invoke the prompt agent | `POST /openai/v1/responses` with `agent_reference` |
| Invoke the hosted agent | `POST /agents/todo-hosted/endpoint/protocols/openai/responses?api-version=v1` |

What the live check (`tests/fixture/test_live_fixture.py`) showed on a freshly provisioned fixture:

- **Prompt agent, no fault.** The response was `completed` with five `openapi_call` items. The
  service recorded read `target`, create `scratch`, read `scratch`, delete `scratch`, update
  `target`, all successful. The strict task outcome was success.
- **Hosted agent, no fault.** The response was `completed` with five `function_call` items. The
  service recorded the same five calls. The strict task outcome was success.
- **Prompt agent, failing tool switched to update for that Attempt.** The service recorded the
  first four calls as successful and the update as HTTP 503 with `fault_injected` true. `target`
  stayed not done and the strict task outcome was failure. The caller did not get a response: the
  invocation returned HTTP 400 `tool_user_error`.
- **Hosted agent, same fault.** The service evidence was the same: four successful calls, then the
  faulted update, strict task outcome failure. The response status was still `completed`.
- **Read without an item identifier** against the deployed service, sent with the hosted agent's
  tool code from the test, not by an agent: HTTP 400, recorded as a rejected read. No agent sent
  such a read in these Attempts.
- **Teardown.** Both resource groups created for this work, `rg-are-42-ee350b0b` and
  `rg-are-42-39e18f83`, were deleted, and a separate `az group exists` returned `false` for each.
  The soft-deleted Foundry accounts were purged.

The same five checks also passed on the first fixture after the fault below was fixed, so the two
no-fault observations and the two fault observations were each seen twice.

One defect was found and fixed on the way. The first prompt agent version was registered with an
OpenAPI document whose update body schema had `"required": []`. Every invocation then failed with
HTTP 400 `tool_user_error` and no request reached the service (three invocations). Registering a
new version without the empty list was the only change, and the next invocation worked. The cause
is inferred from that one change.

Timings: provisioning took about 11 minutes. Teardown took 28 minutes for the first group and
about 30 for the second; the container app environment was the last resource to go each time.
The self-contained live check took 43 minutes end to end.

Not verified here: Foundry cloud evaluation as the entry point, the Invocations protocol, more
than one Attempt at a time, behaviour after a container restart, and what the fixture costs.