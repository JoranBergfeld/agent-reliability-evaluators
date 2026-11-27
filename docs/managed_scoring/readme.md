# Foundry-managed scoring

Foundry-managed scoring runs an Evaluator's calculation as a custom evaluator in your own Foundry project, instead of in your own process. The Evaluator sweep still makes the Attempts and groups the evidence. Foundry only calculates.

Only **outcome consistency** can be scored this way in this version. Every other metric is scored in your own process.

A value that comes back from Foundry is reported as an **unverified result**. It cannot be met or not met by an acceptance criterion. Read [what the scorecard shows](#what-the-scorecard-shows) before you choose this scoring path for a release gate.

| | |
| --- | --- |
| Chosen with | `ConfiguredEvaluator(metric_id=..., scoring_path="foundry_managed")` and `run_sweep(managed_scoring=...)` |
| Reported in | `metrics[].scoring_path`, `fidelity`, `limitation` and `managed_scoring` of the machine-readable scorecard |
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
3. It submits the grouped evidence of the metric to one Foundry evaluation run and waits for the run to end.
4. It reports the value Foundry returned.

`FoundryManagedScoring` waits up to `run_timeout_seconds` (900 by default) and asks for the run's status every `poll_interval_seconds` (10 by default). A run that has not ended in time is cancelled and reported as a failure.

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

Evidence larger than 1,900,000 characters is not submitted and the sweep fails. That is the size one run accepted in the earlier probes; a larger one was rejected. Nothing is split or truncated. Batching is not designed.

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
| `managed_scoring.platform_diagnostics` | The threshold, label and pass flag Foundry attached |
| `acceptance` | `not_assessed` when you declared a criterion, with the reason that the value is an unverified result |

**Unverified result.** Whether the number Foundry returns always equals the number the evaluator computed there has not been established. Until a fidelity rule exists, every value from this path is an unverified result and is shown with its limitation. An unverified result is kept as a diagnostic. It cannot pass or fail your acceptance criterion.

**Platform diagnostics.** Foundry attaches a threshold of 0.5 and a pass or fail label to every result, whatever the metric. They are recorded under `platform_diagnostics` and nowhere else. They are not your acceptance criterion and not an assessment by the Evaluator. Foundry also did not enforce the declared score range in the earlier probes, which is why the value is checked against the scale when it is recovered.

The run's link to the Foundry portal is not recorded, because it embeds your subscription identifier. Find the run by its eval and run identifiers.

The human-readable report shows all of this, because it renders every field of the scorecard.

## When managed scoring gives no value

The sweep then stops with `ManagedScoringFailure`. It never scores the metric in your own process instead, and it never reports a zero or an unavailable measurement in place of the missing result. With `artifact_dir`, the failure is raised as a `ReportingFailure` and the attempt-level evidence collected so far is kept there.

| Situation | What you get |
| --- | --- |
| The project cannot be reached or refuses your identity | Failure before any Attempt is made |
| The metric is an unavailable measurement for the evidence, for example no Task has two Attempts | Failure. The evaluator raises, and Foundry then fails the whole run with no result |
| The evidence was altered on the way | Failure, for the same reason |
| The run ended as failed or canceled, or did not end in time | Failure, with the run's error |
| A completed run whose item carries an error, has no result for the metric, or has a score that is not a number | Failure |
| A score outside the scale of the metric | Failure. The score is not reported |
| The metric has no Foundry-managed evaluator, or `managed_scoring` was not given | `ValueError` before any Attempt is made |

Returning an unavailable measurement with its reason and counts through a managed result is not possible in this version. A managed evaluator can only return a number, and one that raises takes the whole run down (observed, see below). A partial scorecard after a failed managed run, and a customer-requested rescore in your own process, do not exist yet either.

This also holds when the sweep's Attempts were cut short by an execution limit or a cancellation and too little was collected. In your own process that gives a partial scorecard. On this path the managed run fails and the sweep stops.

[`recalculate`](../sweep/late-and-corrected-outcomes.md) scores in your own process. It refuses an earlier scorecard that holds a Foundry-managed result, so a correction never turns such a result into one calculated in your process.

## What stays in your project

Each sweep creates one evaluation and one run in your project, and evaluator versions as described above. The offering does not delete them. Your project governs access to them, their retention and their deletion.

## Assumptions

This comes from the specification and is not a confirmed decision. It is implemented as written.

1. **The managed artifact is a Foundry code evaluator, not an endpoint evaluator** (specification assumption 17). An endpoint evaluator would need the offering to host an endpoint that receives your data. A code evaluator limits the canonical definitions to the code-evaluator sandbox.

## Observed live

Observed on 2026-11-27 in Sweden Central, in a disposable Foundry project with **no model deployment**, with Azure CLI 2.93.0, signed in as a user. This section records what happened. It is not what the platform documents, and each item was seen once unless it says otherwise.

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

The live check ([`tests/live/test_managed_outcome_consistency.py`](../../tests/live/test_managed_outcome_consistency.py)):

- **A sweep scored outcome consistency in Foundry.** Eight synthetic Attempts on the fixture's to-do service, four successes and one failure at one Task and three failures at the other. Foundry returned `0.6799999999999999`, where the hand-calculated value is 0.68. The scorecard attributed it to `foundry_managed` as `unverified`. Foundry labelled it `pass` against its threshold of 0.5. The sweep took 51 seconds, registration and the run included.
- **Registration was idempotent and made as the signed-in identity.** Registering twice more left one version with the derived code. Foundry recorded the signed-in user as its creator.
- **An unavailable measurement was an explicit failure.** With one Attempt at each Task the run ended as failed with `UserError: All examples failed due to invalid user input; please check the individual sample outputs for details.` and no output item. The sweep raised `ManagedScoringFailure`.

The code-evaluator sandbox:

- **It ran Python 3.12.3.** Read with the `platform` module. `import sys` is refused.
- **Accepted modules:** `__future__`, `bisect`, `collections`, `dataclasses` (with `kw_only`), `enum`, `fractions`, `hashlib`, `itertools`, `json`, `math`, `platform`, `re`, `typing`. That covers everything the canonical definitions and the Evidence model import.
- **Refused modules:** `sys`. Decision ticket #12 observed `socket` refused on 2026-11-23.
- **Refused calls:** `compile`, `eval`, `exec`, `globals` and `__import__`. The check goes by the name that is called, so `re.compile(...)` was refused as `use of builtin 'compile()'`. The same text in a comment or a string was accepted. A refused source fails the run before anything executes, with `invalid_grader`.
- **The Evidence model did not fit as it was.** Its confidence patterns used `re.compile`. They are now matched with `re.fullmatch` and `re.findall` on the pattern text, which the sandbox accepted.
- **Accepted:** `getattr`, `setattr`, `hasattr`, `type`, `isinstance` and `object.__setattr__`, which frozen dataclasses use.
- **The derived artifact ran.** It was 51,872 bytes and returned `0.36` for the task outcomes 1, 1, 1, 1, 0.

Registration and runs:

- **No model deployment was needed** to register a code evaluator or to run it.
- **The preview header is required to register.** Without it: HTTP 400, `PreviewFeatureRequired`. Listing versions worked without it.
- **Registering the same code twice created versions 1 and 2.**
- **Versions are listed newest first, ten to a page.** The answer carries `nextLink`, an absolute URL into the same project with a continuation token, or an empty string on the last page. A name that was never registered lists as empty, with HTTP 200. Seen with 130 versions of one name.
- **A criterion can name the version to run.** With `evaluator_version: "1"` the run used version 1 while version 3 was the newest, and without it the run used the newest. An evaluation whose criterion names a version that does not exist was refused with HTTP 404.
- **`desirable_direction: "neutral"` was accepted.**
- **A run was `queued`, then `in_progress`, then `completed`.** Runs with one code evaluator and one item took 16 to 35 seconds.
- **A whole-number score came back as an integer:** an evaluator that returned `1.0` gave `1`.
- **The run's `report_url` embeds the subscription identifier**, in encoded form.
- **Teardown.** The resource groups created for this work were deleted and a separate `az group exists` returned `false` for each. They are named in the pull request that added this page.

## Not verified

- **Fidelity of the returned value.** No probe compared the number computed inside the evaluator with the number received. Every managed value stays unverified.
- **Sign-in as a service principal or a federated workload identity**, and a token function built on `azure-identity`. Only a user signed in to the Azure CLI was used.
- **Which role is needed.** The identity had Foundry User on the account and Foundry Project Manager on the project. Neither was tried alone.
- **Token renewal.** A new token is asked for every five minutes and once more after an HTTP 401. No run was long enough to need it.
- **The sandbox limits.** Microsoft documents no network access, code under 256 KB, 120 seconds per call and 2 GB of memory. Only the refused `socket` and `sys` imports were seen. The size limit was not approached.
- **More than one derived evaluator in one run**, and evidence near the size limit.
- **Two pipelines registering at the same moment.** Both could register a version with the same code. Either is the derived code, so the result would be the same, but the project would hold a duplicate.
- **What the Foundry portal shows** for the evaluator, the run or its result.
