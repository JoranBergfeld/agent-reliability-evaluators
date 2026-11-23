# Disposable Foundry managed-code evaluator prototype

**Managed custom calculation works, including grouped attempt evidence. Reporting
and exact numeric preservation still need care.** This is throwaway code and live
service evidence, not the finished reliability offering or a production SDK.

Executed 2026-11-23 in the authenticated Azure CLI **Sandbox** subscription, in
Sweden Central. The complete trial registered 11 custom code evaluators and
performed 32 probes: 27 runs were created, 16 completed, 11 failed, and five
requests were rejected. Expected negative probes are not implementation failures.
All three uniquely named resource groups, including the two interrupted trials,
were deleted and their absence verified.

## Primary-source assets

- [Complete observations](observations-cbee5be8a2.json): evaluator definitions,
  submitted evidence, evaluation/run IDs, final run states, all output-item pages,
  errors, API versions and verified cleanup.
- [Offline prototype scorecard](scorecard.html): actual values and expandable
  submitted/returned evidence, without combining metrics or treating service
  pass/fail labels as customer acceptance. This is **not** a Foundry-native UI.
- [First interrupted trial](observations-first-88aae1bae5.json) and
  [second interrupted trial](observations-71d5a53a8b.json): TLS failures and
  verified cleanup. Do not treat their incomplete states as final results.
- [Live probe](prototype.py) and [offline renderer](scorecard.py).

Personal creator fields are omitted. Large synthetic padding strings are replaced
in the snapshots with their UTF-8 byte count and SHA-256, rather than committing
megabytes of padding. No credentials or access tokens are persisted.

## What actually ran in Foundry

| Probe | Observed result |
| --- | --- |
| Scalar code evaluator | Submitted 0.25 and 1.0; returned 0.25 and 1.0. |
| Nested attempt arrays | Four outcomes `[1,1,1,0]` returned outcome consistency 0.25; `[1,1,1,1]` returned 1.0. |
| JSON-encoded attempt evidence | `json.loads(item["attempts_json"])` calculated the same 0.25 and 1.0 inside the managed sandbox. |
| Whole-campaign item | Both task groups in one item returned the equal-task calculation 0.625. This is arithmetic inside one invocation, not a cross-row reducer. |
| Separate evaluators together | Outcome consistency and an independent scalar contract evaluator returned separate scores in the same run; both also ran independently. |
| Flat field mapping | `{"renamed":"{{item.value}}"}` with a flat evaluator data schema returned 0.375; output retained the original field and the mapped field. |
| Explicit mapping with the guide's `required:["item"]` schema | Mapping only `renamed` was rejected because `item` was missing. Mapping `{{item}}` was rejected by the mapping-expression grammar. No mapping was needed for the working nested/serialized probes. |
| Inline and uploaded JSONL | Both worked. The uploaded file used `{"item": {...}}` rows and the OpenAI files API, not the versioned Foundry datasets API. Uploaded output items had a different evidence shape: an `item` object plus flattened `item.*` fields. |
| NumPy | Version 2.2.4 was usable; its mean of `[0.25,1.0]` returned 0.625. |
| Networking API | A source containing `import socket` failed with `invalid_grader`: the module is not allowed. No live connectivity test occurred. |
| Large inline row | A 1,900,000-character ASCII padding field worked; 2,100,000 characters plus request overhead was rejected with HTTP 413 from run-history storage. This brackets a limit, not its exact byte boundary. |
| Escaping and Unicode | JSON-string inputs containing whitespace, backslashes, quotes, a newline and a snowman character ran successfully and were retained exactly. |

The only implemented paper metric here is outcome consistency:
`(2 * successful_attempts / attempt_count - 1) ** 2`, with an equal-task
average computed explicitly for the campaign item. The other evaluators isolate
platform contracts; they are not additional validated paper metrics.

## Material differences from the research

### Initialization parameters are governed by the evaluator definition

Omitting a parameter declared required by the documented evaluator definition
was rejected. However, custom definitions declaring only `deployment_name`
required, or declaring **no required initialization parameters**, registered and
ran. An empty initialization-parameters object returned 0.625 successfully.

This does **not** make reporting measurement-only: both variants still returned
`threshold: 0.5`, `label: "pass"` and `passed: true`. A model deployment existed
in this project, so this trial does not establish that a project without any
deployment works. The result refines the research's unconditional requirement
claim, but does not solve imposed reporting semantics.

### Exceptions did not produce a usable unavailable result

Single-item probes with missing attempts, insufficient attempts, null outcomes,
duplicate identities, an explicit exception, a `None` return, an object return,
or a 130-second sleep failed the run. Their output-item API returned an empty
list. Most reported:

> All examples failed due to invalid user input; please check the individual sample outputs for details.

The `NaN` and infinity return probes failed with system/internal errors. The
earlier multi-error trial encountered a JSON-compliance `nan` system error.
We did **not** observe the guide's promised per-item `0.0` plus error through
this output-item endpoint. A mixed valid/invalid run, underlying backend job
artifacts, or portal diagnostics might expose more; those are not established
here. Never substitute zero for these failures.

### Structured numeric evidence is not bit-for-bit faithful

The input `0.83` became `0.8300000000000001` in returned structured evidence and
in the identity evaluator's score. A confidence of `0.49999999999999994`
became `0.49999999999999906` in returned attempt objects. The distinct input
values `0.8300000000000001` and `0.83` became identical output scores.

By contrast, every tested `attempts_json` string was returned unchanged, including
the original confidence/outcome pairs and Unicode/escaping. Opaque serialized
evidence is a demonstrated preservation path for these samples, not a guarantee
for arbitrary input sizes or all service versions. Score precision and the
precision used by every metric calculation remain separate concerns.

### The declared score range was not enforced in these probes

The identity grader returned -0.1 and 1.1 successfully despite metric metadata
declaring a 0-to-1 range. Their scores were not clipped or rejected. Validation
belongs in the evaluator; platform metadata is not a numerical validity guard.

## Reporting and integration points

Authenticate with an Entra bearer token obtained through:

```powershell
az account get-access-token --subscription <sandbox-subscription-id> --resource https://ai.azure.com
```

Keep the token in memory. In this trial, the submitting user and disposable
managed identities had the Foundry User role at the new account scope. This is a
working setup, not proof that every assigned identity needed that role.

The project endpoint is
`https://<account>.services.ai.azure.com/api/projects/<project>`.

| Operation | Route relative to the project endpoint |
| --- | --- |
| Register a custom evaluator version | `POST /evaluators/<name>/versions?api-version=v1` with `Foundry-Features: Evaluations=V1Preview` |
| Create an evaluation and select independently registered criteria | `POST /openai/v1/evals` |
| Submit precomputed inline evidence or a file ID | `POST /openai/v1/evals/<evaluation-id>/runs` |
| Poll final status and inspect error/counts | `GET /openai/v1/evals/<evaluation-id>/runs/<run-id>` |
| Retrieve row-level scores and retained input fields | `GET /openai/v1/evals/<evaluation-id>/runs/<run-id>/output_items`, paginating with `after` |
| Upload the tested JSONL wrapper shape | `POST /openai/v1/files`, multipart `purpose=evals` |

The service exposed backend Azure OpenAI sub-run IDs and evaluation-artifact
pointers in run properties. These were observed diagnostics, **not** tested
or endorsed additional integration contracts.

The two nested task rows produced `result_counts` of one passed and one failed
at the probe threshold 0.5. This is a 50% pass rate, not the paper's equal-task
outcome-consistency value 0.625. The run aggregate did not report that arithmetic
mean. Individual output items exposed the numeric score, label, threshold,
status and original/mapped data. They did not add evaluator-returned evidence
counts, unavailable reasons or arbitrary properties.

The offline scorecard demonstrates a client projection of those API results,
keeping measurements separate from service acceptance labels and showing
submitted versus returned evidence. It does not establish that the native
Foundry portal can suppress labels or present the same scorecard. `report_url`
values were captured, but those project links no longer work after cleanup.

## Reproduce intentionally

**The live command provisions billable resources.** It is fixed to the approved
Sandbox subscription and refuses to reuse an existing resource group. It uses
synthetic evidence, a capacity-one Global Standard `gpt-4.1-mini` deployment
(`2025-04-14`), bounded polling, and deletion in `finally`. Abrupt termination
of the process can bypass `finally`; the printed resource-group name is the
cleanup target in that case.

From the repository root:

```powershell
uv run --python 3.13 --script .\prototypes\foundry-managed-evals\prototype.py
```

It writes a uniquely named `observations-<suffix>.json`. To render a captured
trial without any Azure operations:

```powershell
uv run --no-project --python 3.13 python .\prototypes\foundry-managed-evals\scorecard.py .\prototypes\foundry-managed-evals\observations-cbee5be8a2.json
```

Double-click `scorecard.html`. No server, external scripts or login are needed.

The Azure CLI version was 2.93.0. Python was 3.13.18. PyPI downloads for the
initial pinned SDK attempt failed with TLS handshake errors; SDK invocation was
not validated. The final probe used standard-library REST and Azure CLI auth,
with bounded retries for idempotent reads or TLS failures before request
transmission. Ambiguous writes are not automatically retried.

## Not established

Native portal rendering/export fidelity; a project with no deployed model;
all twelve paper metrics and their edge cases; actual agent invocation, repeated
attempt isolation, fault injection or cross-run reduction; versioned dataset
upload/download; customer-hosted endpoint evaluators; conversation-level
execution; exact runtime/memory/disk/CPU/code-size limits; production workload
scale, service SLAs and complete billing. No production integration or fallback
was chosen on the customer's behalf.

## Documentation compared with the live service

- [Pinned originating research](https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/274fb3bd1997f46c01d4b8a2ab67bb78fcf99e0d/docs/research/2026-11-23-foundry-managed-multi-attempt-evaluation.md).
- [Other custom evaluators](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/other-custom-evaluators).
- [Cloud dataset evaluation](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-datasets).
- [Cloud evaluation results](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-results).
- [Evaluation permissions](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/evaluation-permissions).
- [Evaluation regions and limits](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-regions-limits-virtual-network).

The observations are the primary source for claims of execution. Documentation
describes the intended contract, not proof that every stated behavior occurred.
