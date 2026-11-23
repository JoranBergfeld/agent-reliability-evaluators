# Disposable to-do evaluation plumbing

This is a **throwaway investigation**, not production infrastructure or an
implementation of the full reliability scorecard. It uses synthetic data only.
The results, including unsuccessful and unverified paths, belong in the
corresponding `observations-*.json` file and the linked issue discussion.

## Current result: agent execution not yet validated

On 2026-11-23 four disposable account-creation attempts returned Azure
`InternalServerError`: one in Sweden Central with project management enabled
at creation, one there with project management disabled initially, one in
East US 2 with project management disabled initially, and one in Sweden Central
without requesting a managed identity at creation. The later identity and
project-management steps were therefore not reached. The account errors are
recorded in the observation files. **None of the four agent/entry-point combinations, managed
calculations, or live fault behavior were exercised.** A service-side 500 does
not prove any of those features is unsupported.

The local service was exercised with synthetic CRUD operations, separate
partitions, and an injected 503. Those checks do not establish Foundry tool
integration or isolation. The remaining runner and hosted-agent code are
unvalidated scaffolding and may require changes when Azure provisioning works.
All four temporary resource groups were deleted; independent Azure CLI checks
confirmed that each no longer exists.

A targeted account-creation retry in Sweden Central subsequently succeeded
on 2026-11-23: the account reached provisioning state `Succeeded` at
approximately 21:00 UTC. Its resource group `rg-todo-retry-6c8eb14eb3` was
deleted and independently confirmed absent. The prior 20:50 UTC account write
failed with correlation ID `d553b282-9d20-49df-a0ad-01bf75aa63a7`; the activity
log supplies no more specific error. The cause of the earlier failures remains
unknown. This retry tested account creation only, not the agent matrix or the
remaining scaffold. Final charges were not checked.

## What this probes

A single, externally reachable, API-key-protected to-do service holds separate
in-memory partitions for each attempt. A separate administration key prepares
and reads each partition. Each starts with a target and an unrelated item; the
task permits changes only to the target and a temporary item. The service records
tool requests, returned status and body, including a configurable update fault.
Independent before/after snapshots check whether the unrelated item changed.
This tests the fixture's isolation, not an assumed Foundry state-reset guarantee.
Memory may be lost on a service restart; this is not durable storage.

The harness attempts a prompt agent using an OpenAPI tool and a source-deployed
hosted agent using a function that calls the same service. For each, it attempts
direct customer-code invocation and Foundry cloud evaluation, with two baseline
and two fault-condition attempts. Completed executions are grouped by condition,
and independently sourced binary outcomes are passed to a registered
Foundry-managed custom evaluator and calculated in the customer process.
The expected outcome consistency for a group is
`(2 * successful_attempts / attempt_count - 1) ** 2`. Missing execution evidence
is unavailable, not a synthetic failed attempt.

The model's response is not authoritative evidence of external state. A
successful outcome requires the requested CRUD operations, target completion,
deletion of the temporary item, and an unchanged unrelated item. An attempted
unauthorized call and an actual persisted unauthorized change are recorded
separately. The 503 fault is only considered encountered when the service
records it. No severity assessment is implied by these observations.

## Run intentionally

**This command creates billable Azure resources. Obtain approval first.** It
checks the requested subscription is the approved Sandbox subscription and
creates a uniquely named resource group in Sweden Central (or East US 2 when
`TODO_LOCATION=eastus2`). It uses Azure CLI
2.93, Python 3.13, standard-library REST, a capacity-one Global Standard
`gpt-4.1-mini` deployment, and a Container Apps source build. Hosted agent
dependencies are resolved by the managed remote build and are not pinned.

```powershell
$env:TODO_SANDBOX_SUBSCRIPTION_ID = az account show --query id -o tsv
uv run --no-project --python 3.13 python .\prototypes\todo-plumbing\run.py
```

The script writes synthetic observations as it progresses and deletes the
resource group in `finally`, then verifies its absence. Abrupt process or machine
termination can bypass cleanup: delete only the printed resource group and
verify that it no longer exists. Access tokens and generated service keys are
not intended to be saved. No production service, pre-existing resources, or
customer data should be used.

## Platform documentation

Reviewed 2026-11-23. Documentation describes intended capabilities, not proof
that a particular combination worked in this experiment.

- [Cloud evaluation of agent targets](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-targets)
- [OpenAPI tools and authentication](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/openapi)
- [Source-deployed hosted agents](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent-code)
- [Hosted sessions and isolation](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/manage-hosted-sessions)
- [Managed code evaluators](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/other-custom-evaluators)
- [Cloud evaluation results](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-results)
