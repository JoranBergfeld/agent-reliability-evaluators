# Foundry-managed scoring

Foundry-managed scoring runs an Evaluator's calculation as a custom evaluator in your own Foundry project, instead of in your own process. The Evaluator sweep still makes the Attempts and groups the evidence. Foundry only calculates.

Only **outcome consistency** can be scored this way in this version. Every other metric is scored in your own process.

A value that comes back from Foundry is reported as an **unverified result**. It cannot be met or not met by an acceptance criterion. Read [what the scorecard shows](#what-the-scorecard-shows) before you choose this scoring path for a release gate.

When Foundry gives no value for a metric, the sweep still returns a scorecard. It is partial, names the missing result with its execution error, and keeps the evidence and every other metric: see [when managed scoring gives no value](#when-managed-scoring-gives-no-value). You can then ask for the same evidence to be scored in your own process: see [a customer-process rescore](#a-customer-process-rescore). That never happens by itself.

| | |
| --- | --- |
| Chosen with | `ConfiguredEvaluator(metric_id=..., scoring_path="foundry_managed")` and `run_sweep(managed_scoring=...)` |
| Reported in | `metrics[].scoring_path`, `fidelity`, `limitation`, `managed_scoring` and `execution_failure` of the machine-readable scorecard, and `metrics[].customer_process_rescore` after a rescore |
| Source | [`src/reliability_evaluators/managed_scoring/`](../../src/reliability_evaluators/managed_scoring/) |
| Needs | A Foundry project you can register evaluators in, and a bearer token for it. No model deployment and no extra Python package |

## Using it

```python
from reliability_evaluators.managed_scoring import FoundryManagedScoring, FoundryProject
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, run_sweep

scorecard = run_sweep(
    tasks=[Task(task_id="close", prompt="Close the item.", success_rule=item_is_closed, attempts=5)],
    agent_target=my_agent_target,
    metrics=[ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")],
    managed_scoring=FoundryManagedScoring(
        project=FoundryProject("https://<account>.services.ai.azure.com/api/projects/<project>"),
    ),
)
```

The scoring path is declared per metric. A metric selected by its identity alone is scored in your own process, so one sweep can use both paths. The sweep scores each metric on the path you declared and on no other.

What the sweep does for a metric on this path:

1. Before any Attempt is made, it registers the derived evaluator in your project. A project it cannot use therefore costs no Attempts.
2. It makes the Attempts as it always does.
3. It submits the grouped evidence of the metric to a Foundry evaluation run of its own and waits for the run to end.
4. It reports the value Foundry returned, or that Foundry returned none.

**Each metric has a run of its own.** An evaluator that raises fails its whole run, and takes the results of every other evaluator in that run with it (observed, see below). So no two metrics share a run: a metric whose run fails costs no other metric its result. The runs of a sweep are all started before the first is waited for.

`FoundryManagedScoring` waits up to `run_timeout_seconds` (900 by default) for each run, counted from when that run was made, and asks for the run's status every `poll_interval_seconds` (10 by default). A run that has not ended in time is cancelled and its metric has no result.

To register from a separate CI step, without a sweep:

```python
FoundryManagedScoring(project=my_project).register(["outcome_consistency"])
```

## Your identity

Every request goes to your project as the identity of the bearer token you supply. The offering has no identity of its own and stores no token.

`FoundryProject(endpoint, token=...)` takes a function that returns a token for `https://ai.azure.com`.

- **Default: the Azure CLI.** `azure_cli_token` runs `az account get-access-token --resource https://ai.azure.com`. In CI this is whatever your pipeline signed in as before the sweep, for example with `az login --service-principal` or your platform's federated sign-in.
- **Your own function.** Pass any function that returns a token, for example one built on `azure-identity` if your project already uses it.

**The choice made here:** the project is called over its REST API with the Python standard library (`urllib`), and the token comes from a function you choose. No Azure SDK is used and no package is needed. The Azure CLI is a tool on the runner, not a Python dependency, and you can replace it with your own token function.

Foundry does not accept API keys for evaluations (documented by Microsoft, not tested here).

## How the evaluator is derived

There is one definition of outcome consistency: [`evaluators/outcome_consistency.py`](../../src/reliability_evaluators/evaluators/outcome_consistency.py). No second formula exists for Foundry.

A Foundry code evaluator is one Python source that defines `grade(sample, item)` and returns a number. It cannot import the offering. `derive_artifact("outcome_consistency")` therefore builds that one source from the files your own process executes:

1. It starts from the canonical definition and from [`managed_scoring/grader.py`](../../src/reliability_evaluators/managed_scoring/grader.py), which reads the transported evidence with the Evidence model's own reader.
2. It follows their imports of other modules of the offering, and reads the source of each.
3. It joins those sources in import order. Every statement is kept as written. Only the imports between the joined modules, the module docstrings and `__all__` are left out, because one source does not need them.
4. It appends `grade`, which calls the canonical `evaluate` on the decoded evidence and returns the value.

The artifact is derived when it is needed and is never stored in the repository, so there is no generated file that could fall behind. The Evaluator-seam vectors of outcome consistency run against both the canonical definition and the derived artifact in [`tests/evaluators/test_outcome_consistency_managed_artifact.py`](../../tests/evaluators/test_outcome_consistency_managed_artifact.py). A change that made the two scoring paths give different values fails there.

The derivation refuses, with a `DerivationError`, anything that would only fail later in Foundry:

| Refused | Why |
| --- | --- |
| An import of a module that the sandbox was not observed to accept | The sandbox has a fixed package list |
| A call named `compile`, `eval`, `exec`, `globals` or `__import__`, as a function or a method | The sandbox refuses such a source before running it. `re.compile` is refused too |
| Syntax newer than Python 3.12 | The sandbox ran Python 3.12 when observed |
| A source of 256 KB or more | The documented code-size limit. The artifact is about 71 KB |
| Two joined modules that define the same top-level name, an import under another name, an import of a module as an object, or an import inside a function | One joined source cannot keep them apart |

The canonical definitions and the Evidence model therefore have to stay inside those rules.

### Adding another Evaluator

Declare the Attempt evidence it is calculated from in `MANAGED_ATTEMPT_EVIDENCE` in [`artifact.py`](../../src/reliability_evaluators/managed_scoring/artifact.py), by the field names of the Attempt record. The same derivation then produces its artifact, and the same reader reads its evidence. Then run its Evaluator-seam vectors against the derived artifact, as the outcome consistency test does. That test is what shows the declared evidence is enough. Only outcome consistency has been derived and proven. An Evaluator that also takes a customer declaration, such as the action types of the trajectory metrics, needs that declaration transported too, which nothing does yet.

## Registration

The evaluator is registered as `reliability-outcome-consistency`, of type `custom` with a `code` definition.

Registration is **idempotent**. Foundry itself is not: registering the same code twice creates a second version (observed). So the offering first lists the versions your project holds under that name and compares their code with the derived code:

- A version with exactly the derived code exists: it is used. Nothing is registered.
- None exists: a new version is registered.

The evaluation run then names that version explicitly. An evaluator of the same name with other code, whether older, newer or registered by someone else, is never run in its place. When you move your pin of the offering to a commit that changes the derived code, the next sweep registers a new version. Older versions stay in your project until you delete them.

The declared range of the score is the scale of the canonical definition. The desirable direction is declared as `neutral`, because the canonical definition does not say that higher is better.

## The evidence that is submitted

For each Attempt the evaluator is given: its identity, its Task, its execution status and its task outcome with its source. Nothing else about the Attempt is sent: its actions, its resource measurements, its confidence rating, its violation assessments and what it records about its experiment stay in your process. It goes to your own project.

Each Attempt is written exactly as the Attempt record writes itself in the scorecard, with the evidence that is not sent left empty, and is read back with the Evidence model's strict reader (`read_attempts`). That reader accepts an Attempt only when it is exactly what this version records.

The evidence travels as one string in one field, `outcome_consistency_evidence`: a SHA-256 digest, a colon, and the evidence as JSON.

- **A string, not structured data.** Structured numbers were altered on the way into a managed evaluator, and strings were not, in the earlier probes (decision ticket #12).
- **A digest.** The evaluator recomputes the digest before it reads anything. Evidence that changed on the way is rejected inside Foundry. It can never produce a value calculated from other evidence.

The evaluator validates what it receives with the same code as your own process: the Evidence model rejects a task outcome that is not binary, an Attempt without an identity and two Attempts with the same identity, and the result contract rejects a value outside the scale. A field this version does not know is rejected, not ignored.

Evidence larger than 1,900,000 characters is not submitted, and the metric has no result. That is the size one run accepted in the earlier probes; a larger one was rejected. Nothing is split or truncated. Batching is not designed.

## What the scorecard shows

The metric's entry has the same fields as a customer-process entry, and these differences:

| Field | Content |
| --- | --- |
| `value` | The number Foundry returned. It is never calculated in your own process |
| `scoring_path` | `foundry_managed` |
| `fidelity` | `unverified` |
| `limitation` | Why the value is unverified and what is not reported, in words |
| `counts`, `details`, `evidence.attempt_ids` | Empty. Foundry returns one number and nothing else the evaluator computed |
| `attempts_used` | The Attempts the evaluator was given, as on the other path |
| `managed_scoring.evaluator` | The registered name, the version that ran and the SHA-256 of its code |
| `managed_scoring.eval_id`, `run_id`, `output_item_id` | Where the value came from in your project |
| `managed_scoring.evidence_sha256` | The digest the evidence was submitted with: the SHA-256 of the evidence as it was sent |
| `managed_scoring.platform_diagnostics` | The threshold, label and pass flag Foundry attached |
| `acceptance` | `not_assessed` when you declared a criterion, with the reason that the value is an unverified result |

**Unverified result.** Whether the number Foundry returns always equals the number the evaluator computed there has not been established. Until a fidelity rule exists, every value from this path is an unverified result and is shown with its limitation. An unverified result is kept as a diagnostic. It cannot pass or fail your acceptance criterion.

**Platform diagnostics.** Foundry attaches a threshold of 0.5 and a pass or fail label to every result, whatever the metric. They are recorded under `platform_diagnostics` and nowhere else. They are not your acceptance criterion and not an assessment by the Evaluator. Foundry also did not enforce the declared score range in the earlier probes, which is why the value is checked against the scale when it is recovered.

The run's link to the Foundry portal is not recorded, because it embeds your subscription identifier. Find the run by its eval and run identifiers.

The human-readable report shows all of this, because it renders every field of the scorecard.

## When managed scoring gives no value

A project that cannot be used stops the sweep before any Attempt is made, with `ManagedScoringFailure` or a `ValueError`. Nothing was collected, so there is nothing to keep. With `artifact_dir` it is raised as a [`ReportingFailure`](../report/readme.md#reporting-failure) that has that error as its cause.

After the Attempts, the sweep does not stop. It returns a scorecard that is **partial**:

- The metric has no result. Its `state`, `value` and `fidelity` are `null`. It is not reported as an unavailable measurement, as a value of zero or as a task failure: nothing was measured, and no task outcome is changed.
- `execution_failure.reason` says that Foundry-managed scoring gave no result, followed by the execution error. `managed_scoring.error` holds the execution error on its own, next to how far the run got: the evaluator, the evaluation, the run and the digest of the submitted evidence, each `null` if it was not reached.
- `missing` lists the result with the kind `metric_result` and the same reason.
- Every Attempt is in the scorecard as it was collected.
- Every other metric keeps its own result, whether it was scored in your own process or in another managed run.
- The metric is never scored in your own process instead. An acceptance criterion on it is `not_assessed`, with the failure as the reason.

```json
{
  "metric_id": "outcome_consistency",
  "state": null,
  "value": null,
  "reason": null,
  "execution_failure": {
    "reason": "Foundry-managed scoring gave no result for this metric, and it was not scored in the customer's own process instead: the Foundry-managed run evalrun_1 of eval_1 ended as failed: UserError: All examples failed due to invalid user input; ...",
    "experiment_ids": [],
    "task_ids": []
  },
  "scoring_path": "foundry_managed",
  "fidelity": null,
  "managed_scoring": {
    "evaluator": {"name": "reliability-outcome-consistency", "version": "1", "code_sha256": "809f..."},
    "eval_id": "eval_1",
    "run_id": "evalrun_1",
    "output_item_id": null,
    "evidence_sha256": "dd62...",
    "error": "the Foundry-managed run evalrun_1 of eval_1 ended as failed: UserError: All examples failed due to invalid user input; ..."
  }
}
```

`execution_failure` is the field a metric without a result always has. Its `experiment_ids` and `task_ids` are empty here, because no Attempt was cut short: it is the scoring that failed. A metric whose [Attempts were cut short](../sweep/execution-limits.md#metrics-in-a-partial-scorecard) has no `managed_scoring.error`.

With `artifact_dir`, the partial scorecard, its report and the evidence are delivered as for any sweep: see [delivery alongside Foundry](../report/delivery-alongside-foundry.md#partial-and-failed-managed-runs).

| Situation | What you get |
| --- | --- |
| The project cannot be reached or refuses your identity when the evaluator is registered | The sweep stops before any Attempt is made |
| The metric has no Foundry-managed evaluator, or `managed_scoring` was not given | `ValueError` before any Attempt is made |
| The metric is an unavailable measurement for the evidence, for example no Task has two Attempts | No result. The evaluator raises, and Foundry then fails its run |
| The evidence was altered on the way | No result, for the same reason |
| The evidence is larger than one run accepts | No result. Nothing was submitted |
| The project refuses or does not answer a request after the Attempts were made | No result, with the request and its answer |
| The run ended as failed or canceled, or did not end in time | No result, with the run's error |
| A completed run whose item carries an error, whose result is in the status `error`, that has no result for the metric, or whose score is not a number | No result |
| A score outside the scale of the metric | No result. The score is not reported |
| Managed scoring stops on an error of its own | No result for every metric on this path, with that error |

Returning an unavailable measurement with its reason and counts through a managed result is not possible in this version. A managed evaluator can only return a number, and one that raises fails its run. Such a metric therefore has no result, and the scorecard does not call it unavailable, because nothing came back that says so. This also holds when the sweep's Attempts were cut short by an execution limit or a cancellation and too little was collected.

## A customer-process rescore

A customer-process rescore scores, in your own process, the evidence that Foundry-managed scoring was given for a metric. You ask for it, for the metrics you name. No sweep and no recalculation does it by itself, whatever became of the managed run.

```python
import json
from pathlib import Path

from reliability_evaluators.scorecard import ArtifactLocation
from reliability_evaluators.sweep import RescoreFailed, rescore_in_customer_process

earlier = json.loads(Path("artifacts/reliability/scorecard.json").read_text(encoding="utf-8"))

try:
    scorecard = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])
except RescoreFailed as failure:
    print(failure)  # what stopped it. There is no new scorecard, and the earlier one stands.
    raise

ArtifactLocation("artifacts/reliability-rescored").deliver(scorecard)
```

Like a [recalculation](../sweep/late-and-corrected-outcomes.md#you-supply-the-earlier-scorecard), it works from the earlier `scorecard.json` that you supply from your platform, which holds the attempt-level evidence. It makes no Attempt, calls no platform and does not change the scorecard you pass in. Deliver the result to a directory of its own.

| Argument | Content |
| --- | --- |
| `earlier_scorecard` | The scorecard of the sweep, read from its JSON. |
| `metrics` | The metrics to rescore, by metric identity. At least one. Each has to be a metric that the scorecard attributes to Foundry-managed scoring, with a value or with a failure. Give the same `ConfiguredEvaluator` as the sweep when the Evaluator has declarations of its own. |
| `action_evidence` | Declared again as for the sweep, when a rescored metric needs it. |
| `acceptance_criteria` | Declared again as for the sweep. Required for a rescored metric that the earlier scorecard assessed a criterion for. |

### What the new scorecard holds

The new scorecard is the earlier one with one addition per rescored metric: `customer_process_rescore`, inside the entry of that metric. Nothing else changes. The example is shortened.

```json
{
  "metric_id": "outcome_consistency",
  "state": null,
  "value": null,
  "execution_failure": {"reason": "Foundry-managed scoring gave no result for this metric, ...", "experiment_ids": [], "task_ids": []},
  "scoring_path": "foundry_managed",
  "fidelity": null,
  "managed_scoring": {"evidence_sha256": "dd62...", "error": "the Foundry-managed run ... ended as failed: ..."},
  "acceptance": {"status": "not_assessed", "reason": "the metric has no result because of an execution failure: ..."},
  "customer_process_rescore": {
    "meaning": "A customer-process rescore: the customer asked for the evidence that Foundry-managed scoring was given for this metric to be scored in their own process. ...",
    "evidence_sha256": "dd62...",
    "state": "available",
    "value": 0.6799999999999999,
    "counts": {"tasks": {"total": 2, "valid": 2, "missing": [], "excluded": []}},
    "scoring_path": "customer_process",
    "fidelity": "authoritative",
    "acceptance": {"status": "met", "assessed_by": "offering"}
  }
}
```

- **The managed result stays.** The entry of the metric still holds what Foundry-managed scoring gave: the unverified value with its limitation, or the failure with its execution error. `metrics[].value` is never the rescored value.
- **A partial scorecard stays partial.** A managed result that is missing is still listed in `missing`. The rescore does not make up for it.
- **The rescore is a result of its own.** It has every field that a metric scored in your own process has, calculated by the same Evaluator from the Attempts the managed evaluator was given: its state, value, definition, counts, details and the Attempts the value rests on. It is attributed to `customer_process` and is `authoritative` for that path. It can be an unavailable measurement, with its reason, or have an execution failure of its own when the Attempts were cut short.
- **Acceptance is assessed separately.** `metrics[].acceptance` is the assessment of the managed result and is not changed: an unverified value or a missing result is still `not_assessed`. Your criterion is also assessed on the rescore, in `customer_process_rescore.acceptance`. Which of the two your release gate reads is your decision. Reading the rescore means deciding on a value from your own process, not on the one Foundry returned.
- **The same evidence.** `customer_process_rescore.evidence_sha256` is the digest of the evidence the rescore was calculated from, in the encoding it is submitted to Foundry in. It equals `managed_scoring.evidence_sha256`, the digest of what was submitted. A scorecard whose attempt-level evidence no longer gives that digest is refused.

The [human-readable report](../report/readme.md) says at the top that the scorecard holds a rescore, and shows the rescore in a block of its own directly below the metric it was requested for, marked as not being the Foundry-managed result.

### When a rescore is refused

A rescore either gives a new scorecard or raises `RescoreFailed`. Nothing is written, and the earlier scorecard stands.

| Situation | The failure says |
| --- | --- |
| No metric is named | `no metric was named` |
| A named metric was scored in your own process | `'...' was scored on the scoring path 'customer_process' in the earlier scorecard` |
| A named metric was not selected in the sweep | `'...' was not selected in the earlier scorecard` |
| A named metric already has a rescore | `'...' already has a customer-process rescore`. The same evidence gives the same result, and the one that is there is not replaced |
| The attempt-level evidence was changed since the sweep | `the supplied evidence is not the evidence that was submitted to Foundry for '...'` |
| The scorecard or an Attempt in it is not what this version records | `the supplied evidence does not validate`, with what is wrong |
| The earlier scorecard assessed an acceptance criterion for a rescored metric, and none is declared | `the earlier scorecard assessed an acceptance criterion for '...'` |
| A rescored metric needs a declaration that was not given | `'...' needs action evidence to be declared` |

Rescore with the commit or tag of the offering that made the scorecard. Another version may record an Attempt differently, and the evidence then does not give the digest that was submitted.

### A recalculation is not a rescore

[`recalculate`](../sweep/late-and-corrected-outcomes.md) applies task outcomes that were supplied or corrected after the run, and scores in your own process. It still refuses an earlier scorecard that holds a Foundry-managed metric, with or without a rescore. A corrected outcome would make the managed result out of date, and scoring it again in your own process would replace the result of the path you chose. Correcting a task outcome behind a Foundry-managed result is not supported in this version: run the sweep again.

## What stays in your project

Each sweep creates one evaluation and one run in your project for every metric on this path, and evaluator versions as described above. The offering does not delete them. Your project governs access to them, their retention and their deletion.

The run keeps the evidence it was sent, also when it failed. The scorecard, the report and the complete attempt-level evidence are not in your project: they are delivered to the directory you chose. What the run holds, how to leave a reference to the scorecard on its evaluation, and what deleting a run did when observed are in [delivery alongside Foundry](../report/delivery-alongside-foundry.md).

## Assumptions

This comes from the specification and is not a confirmed decision. It is implemented as written.

1. **The managed artifact is a Foundry code evaluator, not an endpoint evaluator** (specification assumption 17). An endpoint evaluator would need the offering to host an endpoint that receives your data. A code evaluator limits the canonical definitions to the code-evaluator sandbox.

## Observed live

Observed on 2026-11-27 in Sweden Central, in three disposable Foundry projects with **no model deployment**, with Azure CLI 2.93.0, signed in as a user. This section records what happened. It is not what the platform documents, and each item was seen once unless it says otherwise.

Interfaces used, as they worked on that date:

| Purpose | Request, relative to the project endpoint |
| --- | --- |
| List the versions of an evaluator | `GET /evaluators/<name>/versions?api-version=v1` |
| Register an evaluator version | `POST /evaluators/<name>/versions?api-version=v1` with the header `Foundry-Features: Evaluations=V1Preview` |
| Create an evaluation | `POST /openai/v1/evals` |
| Create a run | `POST /openai/v1/evals/<eval>/runs` |
| Read a run | `GET /openai/v1/evals/<eval>/runs/<run>` |
| Read the output items | `GET /openai/v1/evals/<eval>/runs/<run>/output_items?limit=100` |

The error text of a refused evaluator shows that the project called an Azure OpenAI evals interface with `api-version=2025-04-01-preview` behind these routes.

The live check ([`tests/live/test_managed_outcome_consistency.py`](../../tests/live/test_managed_outcome_consistency.py)) passed three times, in three projects. The last time it provisioned its own project and ran the derived evaluator as it is in this commit: 70,736 bytes, SHA-256 `809f7a6c1d02c24e9792a316f22f8534bdc0522fbdf6e623e141c88f86164b9d`. What it showed each time:

- **A sweep scored outcome consistency in Foundry.** Eight synthetic Attempts on the fixture's to-do service, four successes and one failure at one Task and three failures at the other. Foundry returned `0.6799999999999999`, where the hand-calculated value is 0.68. The scorecard attributed it to `foundry_managed` as `unverified`. Foundry labelled it `pass` against its threshold of 0.5. The sweep took 43 to 52 seconds, registration and the run included.
- **Registration was idempotent and made as the signed-in identity.** Registering twice more left one version with the derived code. Foundry recorded the signed-in user as its creator.
- **An unavailable measurement was an explicit failure.** With one Attempt at each Task the run ended as failed with `UserError: All examples failed due to invalid user input; please check the individual sample outputs for details.` and no output item. The sweep raised `ManagedScoringFailure`, as it did at that time. It now returns a partial scorecard: see [failed and partial runs](#failed-and-partial-runs-observed-on-2026-11-27).

A fourth run did not get as far as the check. The project it had just created answered its first request with HTTP 404 `Project not found`, the provisioning stopped and the resource group was torn down. The provisioning now waits for the project to answer, and the run after that passed.

The code-evaluator sandbox:

- **It ran Python 3.12.3.** Read with the `platform` module. `import sys` is refused.
- **Accepted modules:** `__future__`, `bisect`, `collections`, `dataclasses` (with `kw_only`), `enum`, `fractions`, `hashlib`, `itertools`, `json`, `math`, `platform`, `re`, `typing`. That covers everything the canonical definitions and the Evidence model import.
- **Refused modules:** `sys`. Decision ticket #12 observed `socket` refused on 2026-11-23.
- **Refused calls:** `compile`, `eval`, `exec`, `globals` and `__import__`. The check goes by the name that is called, so `re.compile(...)` was refused as `use of builtin 'compile()'`. The same text in a comment or a string was accepted. A refused source fails the run before anything executes, with `invalid_grader`.
- **The Evidence model did not fit as it was.** Its confidence patterns used `re.compile`. They are now matched with `re.fullmatch` and `re.findall` on the pattern text, which the sandbox accepted.
- **Accepted:** `getattr`, `setattr`, `hasattr`, `type`, `isinstance` and `object.__setattr__`, which frozen dataclasses use.
- **The derived artifact ran.** On its own it returned `0.36` for the task outcomes 1, 1, 1, 1, 0. It was 51,872 bytes then, before it carried the Evidence model's reader.

Registration and runs:

- **No model deployment was needed** to register a code evaluator or to run it.
- **The preview header is required to register.** Without it: HTTP 400, `PreviewFeatureRequired`. Listing versions worked without it.
- **Registering the same code twice created versions 1 and 2.**
- **Versions are listed newest first, ten to a page.** The answer carries `nextLink`, an absolute URL into the same project with a continuation token, or an empty string on the last page. A name that was never registered lists as empty, with HTTP 200. Seen with 130 versions of one name.
- **A criterion can name the version to run.** With `evaluator_version: "1"` the run used version 1 while version 3 was the newest, and without it the run used the newest. An evaluation whose criterion names a version that does not exist was refused with HTTP 404.
- **`desirable_direction: "neutral"` was accepted.**
- **A run was `queued`, then `in_progress`, then `completed`.** Runs with one code evaluator and one item took 16 to 35 seconds.
- **A whole-number score came back as an integer:** an evaluator that returned `1.0` gave `1`.
- **The run's `report_url` embeds the subscription identifier**, as base64url text. This was checked against the subscription.
- **Teardown.** The four resource groups created for this work, `rg-are-46-8d7aa8b7`, `rg-are-46-04bb5845`, `rg-are-46-0af4149d` and `rg-are-46-ab74aa94`, were deleted, and a separate `az group exists` returned `false` for each. The soft-deleted Foundry accounts were purged.
- **Timing.** A run of the live check that provisions its own project took 23 minutes once and 7 minutes once. Nearly all of it was creating and deleting the Foundry account.

### Failed and partial runs, observed on 2026-11-27

Observed in Sweden Central in one more disposable Foundry project with no model deployment, with the same interfaces as above. Each item was seen once unless it says otherwise. The evaluators used for the first five items were test-only code evaluators registered in that project for the purpose: one that returns `0.25`, one that raises, and one that raises for one item text and returns `0.75` otherwise.

- **An evaluator that raises on the only item fails the run.** The run ended as `failed` with `UserError: All examples failed due to invalid user input; please check the individual sample outputs for details.`, `result_counts` of zero and no output item. Seen in a probe and in every sweep that ran the evaluator that raises, but one: once the run of that same evaluator ended as `failed` with `SystemError: Evaluation system error: (InternalError) AOAI evaluation run ... failed with status failed`, also with no output item. The sweep goes by the status of the run and reports whichever error the run carries.
- **It takes the other evaluators of the run with it.** A run with two criteria and one item, where one evaluator returned a number and the other raised, ended the same way: `failed`, no output item, and no result for the evaluator that had nothing wrong with it. This is why each metric has a run of its own.
- **An evaluator that raises on one of two items leaves the run `completed`.** The item it raised on was `completed` with a result in the status `error`, a `null` score, label and pass flag, and the error on the result's own `sample.error`: `ProviderGradingError: An error occurred during grading (provider run ...; item 1)`. The item's own `sample.error` was `null`. `result_counts.errored` was 1. The other item had its score. The same held with a second evaluator in the run, which kept both of its results. The offering submits one item per run, so it does not get such a run. It reads the error on a result all the same.
- **Runs made at the same time all ended.** Five runs started back to back all ended within 133 seconds of the first being made. Twelve runs of the evaluator that returns a number, started back to back, all ended as `completed` within 201 seconds of the first being made, of which making the twelve took 135 seconds with a new token for every request. No request was refused. Twelve derived evaluators with real evidence were not tried.

The live check ([`tests/live/test_managed_scoring_failures.py`](../../tests/live/test_managed_scoring_failures.py)) passed against that project, with the derived evaluator of this commit (SHA-256 `809f7a6c1d02c24e9792a316f22f8534bdc0522fbdf6e623e141c88f86164b9d`). It makes a managed run fail without touching the canonical definition: a test-only evaluator that raises is registered under a name of its own, and the project is told to run it where the sweep names the derived one. What it showed:

- **A failed managed run left a partial scorecard.** Eight synthetic Attempts, as in the check above. The run ended as `failed` with the `UserError` above. The scorecard was `partial`, outcome consistency had no state and no value, `missing` named it with that error, all eight task outcomes were kept, and resource consistency, scored in this process from the same Attempts, kept its value. The scorecard, its report and the evidence were delivered to a directory.
- **A rescore on request stood beside the failure.** Rescoring the delivered scorecard gave `0.6799999999999999`, attributed to `customer_process`, from evidence with the digest that was submitted to the project. The managed failure and the partial status were unchanged.
- **A rescore stood beside an unverified value.** With the derived evaluator left to run, Foundry returned `0.6799999999999999` and the rescore gave `0.6799999999999999`. This is one comparison of one value. It is not a fidelity rule, and the managed value stays unverified.
- **Of two managed runs of one sweep, one failed and the other kept its result.** For this check only, a second Evaluator was put on the managed path and the test-only evaluator was run in its place. Its run ended as `failed`, once with the `UserError` and once with the `SystemError` above. The run of outcome consistency, made at the same time, returned `0.6799999999999999` both times, and the scorecard kept it.
- **The earlier live check still passed** against the same project, with its last case changed: an unavailable measurement now leaves a partial scorecard where the sweep used to raise.
- **Timing.** The three checks took 109 seconds together against a project that already existed, and 215 seconds together with the three of the earlier live check.
- **Teardown.** The resource group created for this work, `rg-are-50-0ee7446d`, was deleted with everything registered in it, and a separate `az group exists` returned `false`. The soft-deleted Foundry account was purged.

## Not verified

- **Fidelity of the returned value.** No probe compared the number computed inside the evaluator with the number received. Every managed value stays unverified.
- **Sign-in as a service principal or a federated workload identity**, and a token function built on `azure-identity`. Only a user signed in to the Azure CLI was used.
- **Which role is needed.** The identity had Foundry User on the account and Foundry Project Manager on the project. Neither was tried alone.
- **Token renewal.** A new token is asked for every five minutes and once more after an HTTP 401. No run was long enough to need it.
- **The sandbox limits.** Microsoft documents no network access, code under 256 KB, 120 seconds per call and 2 GB of memory. Only the refused `socket` and `sys` imports were seen. The size limit was not approached.
- **Twelve derived evaluators, each in a run of its own.** Twelve runs at once were seen only with a test-only evaluator and a one-word item. Whether a project limits how many runs it accepts at once is not known. A run that is refused leaves its metric without a result and the others as they are.
- **Evidence near the size limit, with a derived evaluator.** A test-only evaluator was given items of up to 1,900,000 characters whole: see [delivery alongside Foundry](../report/delivery-alongside-foundry.md#probes). Real evidence of that size was not submitted to a derived evaluator.
- **A managed run that does not end in time, other than at once.** A run that was cancelled while it was still `queued`, with a time limit of 0 seconds, was `canceled` afterwards and left a partial scorecard: see [delivery alongside Foundry](../report/delivery-alongside-foundry.md#the-live-check). A run that is cancelled after it has started was exercised against the fake project only.
- **A completed run whose only item has a result in the status `error`.** Seen only in a run with two items.
- **A request that the live project refuses after the Attempts were made**, such as a rate limit. Exercised against the fake project only.
- **Two pipelines registering at the same moment.** Both could register a version with the same code. Either is the derived code, so the result would be the same, but the project would hold a duplicate.
- **What the Foundry portal shows** for the evaluator, the run or its result.
