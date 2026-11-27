# Delivery alongside Foundry

Where the scorecard, the human-readable report and the attempt-level evidence go when a metric is scored by [Foundry-managed scoring](../managed_scoring/readme.md), what Foundry holds of such a run, and what can be left in Foundry that leads back to the scorecard.

The short answer: an Evaluator sweep runs in your own process whichever scoring path a metric uses. With Foundry-managed scoring only the calculation of that metric runs in Foundry. So the [delivered files](readme.md) are produced in your process and written to the directory you choose on both scoring paths, and that directory is the only place they are. Nothing delivers them into Foundry. A reference to them can be left on the Foundry evaluation, by you.

Everything under [Observed live](#observed-live) was observed on 2026-11-27. It is what happened, not what the platform documents.

## What is where

| | Customer-process scoring | Foundry-managed scoring |
| --- | --- | --- |
| `scorecard.json`, `report.html`, `summary.md`, `evidence.json` | The directory you give as `artifact_dir` | The same directory |
| In your Foundry project, from scoring | Nothing | For each managed metric: one evaluation, its runs and the evaluator version that ran. There are two runs, and more when a part of the result is asked for again |
| What those runs hold | | The evidence they were sent, in every row. The numbers they returned: the plain value with Foundry's own threshold and label, and the [encoded result](../managed_scoring/readme.md#how-a-result-comes-back) in parts |
| From the scorecard to the runs | | `metrics[].managed_scoring`: `eval_id`, `run_id` and `output_item_id` for the plain value, `encoded_result.run_ids` for the rest, and `evidence_sha256` |
| From the runs to the scorecard | | Nothing by itself. You can leave a reference: see [below](#leaving-a-reference-in-foundry) |

An agent target that invokes an agent in Foundry is a separate matter. What the platform keeps of those invocations is not covered here.

## The run or artifact context

The scorecard and the evidence are outputs of the platform the sweep executes on ([ADR 0003](../adr/0003-no-evidence-store.md)). That platform is where your process runs: a CI job, for example. Give `artifact_dir` a directory that the job publishes as a build artifact. Access to the files, their retention and their deletion are then that platform's.

Foundry is not where the sweep executes, also not with Foundry-managed scoring. What a managed run leaves in your Foundry project is governed by that project: reading it takes a token for the project and a role in it, as the identity you reach it with.

The offering keeps no store. It writes to `artifact_dir` and nowhere else on the machine it runs on. To your Foundry project it sends only what managed scoring needs.

## What Foundry holds of a managed run

- **Only the evidence the metric is calculated from.** For outcome consistency that is, for each Attempt, its identity, its Task, its execution status and its task outcome with its source. The responses of the agent, the tool calls, the resource measurements, the confidence evidence and what an Attempt records about its experiment are in `evidence.json` and are not sent. A Foundry run is therefore not a copy of the attempt-level evidence.
- **Not readable as it stands.** The evidence is [submitted](../managed_scoring/readme.md#the-evidence-that-is-submitted) as one string: its SHA-256 digest, a colon, and its JSON, deflated and written in base64. To read it, decode the base64 text and inflate it as raw deflate. The digest is the `evidence_sha256` in the scorecard.
- **The evidence stays on the run, also when the run fails.** A run answers a read with the rows it was made with, each with the evidence. A run that failed has no output item, and still answers with those rows.
- **The output item is not a reliable copy of large evidence.** An output item repeated a submitted field of 900,000 characters whole, and one of 1,000,000 characters as its first 1,000 characters, with nothing that says so. The evaluator was given the whole field both times, and the run itself answered with the whole field. The offering never reads evidence back from Foundry.
- **The numbers and Foundry's diagnostics.** The output item of the `value` row holds the plain value as its score, with the threshold and label that Foundry adds. The scorecard records them as platform diagnostics. The other rows hold the size of the encoded result and its parts as large whole numbers, each with a threshold and a label that mean nothing.

## Leaving a reference in Foundry

A reference to the scorecard can be left on the evaluation of a managed metric, in its metadata, after the scorecard was delivered. The offering makes one evaluation for each managed metric of a sweep, and every run for that metric under it, so the evaluation stands for those runs. The offering does not leave a reference by itself, because it does not know where your platform publishes the directory.

```python
import hashlib
from pathlib import Path

from reliability_evaluators.managed_scoring import FoundryProject

project = FoundryProject("https://<account>.services.ai.azure.com/api/projects/<project>")
reference = {
    "reliability_scorecard": "<where your CI publishes the directory>",
    "reliability_scorecard_sha256": hashlib.sha256(Path("artifacts/reliability/scorecard.json").read_bytes()).hexdigest(),
}
for entry in scorecard["metrics"]:
    managed = entry.get("managed_scoring")
    if managed and managed["eval_id"]:
        project.request("POST", f"/openai/v1/evals/{managed['eval_id']}", {"metadata": reference})
```

The names of the two metadata keys are yours to choose. `eval_id` is `null` when managed scoring did not get as far as making the evaluation.

- **It is a reference, not the scorecard.** Whoever follows it needs access to where it points. Foundry does not check it.
- **It works for a failed run as well.** The evaluation of a run that failed took the reference and returned it.
- **The run itself cannot be given one afterwards.** A run takes metadata only when it is made. No request was found that changes it later: the documented `POST` on a run cancels it. The offering sets no metadata when it makes a run.
- **Keep to the documented limits.** Microsoft documents metadata as 16 key-value pairs, with keys of at most 64 characters and values of at most 512. More was accepted when observed, which is not a reason to rely on it.
- **The scorecard and the report themselves were not attachable to a run.** See [Observed live](#observed-live).

## Partial and failed managed runs

A managed run that gives no result still ends in a delivery: the scorecard is `partial`, in `scorecard.json` and at the top of `report.html`, with the missing result and its execution error, and `evidence.json` holds every Attempt. See [when managed scoring gives no result](../managed_scoring/readme.md#when-managed-scoring-gives-no-result).

A delivery that fails after a managed run is a [reporting failure](readme.md#reporting-failure) like any other. The `ReportingFailure` carries the scorecard, so the result that Foundry returned and the identifiers of its run are not lost with the delivery. The run stays in your project.

A Foundry project that cannot be used stops the sweep before any Attempt is made. With `artifact_dir` that is raised as `ReportingFailure`, caused by the `ManagedScoringFailure`, and `reporting-failure.json` is written.

## Evidence limits

- **A limit that shows at evaluation time is an unavailable measurement.** A metric whose evidence is missing or too little is reported as `unavailable`, with its reason and its evidence counts, in `scorecard.json` and in `report.html`, and never as a number. In the live check the hosted agent target gives no confidence rating, so the Brier-based score was unavailable with the reason `no valid confidence/outcome pair` and a count of the five Attempts that have no rating, each with why.
- **On the managed path as well.** Foundry-managed scoring returns an unavailable measurement as one, with its reason and counts, and the scorecard is not partial because of it: see [how a result comes back](../managed_scoring/readme.md#how-a-result-comes-back). In the live check, outcome consistency with one Attempt at each Task came back from Foundry as unavailable and was delivered as that in both formats.
- **A limit of the transport is not a limit of the evidence.** A managed run that would be larger than one run accepts is not submitted, and a managed run that fails gives nothing. Such a metric has no result and the scorecard is partial. It is not called unavailable, because nothing was measured.
- **Nothing is watched afterwards.** A run or an evaluation that you delete in Foundry is gone, and so is an artifact that your CI platform expires. The scorecard is not revised, and its `managed_scoring` identifiers then lead nowhere.

## Observed live

Observed on 2026-11-27 in Sweden Central, in three disposable fixtures ([`live_fixture/`](../../live_fixture/README.md)), each with a Foundry project, a model deployment and a hosted agent, from a workstation with Azure CLI 2.93.0, signed in as a user. A local directory stood in for the artifact directory of a CI job. Synthetic data only.

Interfaces used, as they worked on that date, relative to the project endpoint:

| Purpose | Request |
| --- | --- |
| Create an evaluation, with `metadata` | `POST /openai/v1/evals` |
| Read an evaluation | `GET /openai/v1/evals/<eval>` |
| Change the metadata of an evaluation | `POST /openai/v1/evals/<eval>` with `{"metadata": {...}}` |
| Create a run, with `metadata` | `POST /openai/v1/evals/<eval>/runs` |
| Read a run | `GET /openai/v1/evals/<eval>/runs/<run>` |
| Read the output items | `GET /openai/v1/evals/<eval>/runs/<run>/output_items?limit=100` |
| Cancel a run | `POST /openai/v1/evals/<eval>/runs/<run>/cancel` |
| Delete a run, delete an evaluation | `DELETE /openai/v1/evals/<eval>/runs/<run>`, `DELETE /openai/v1/evals/<eval>` |
| Upload a file | `POST /openai/v1/files`, multipart, with `purpose` and `file` |
| Read the result asset of a run | `GET /evaluationresults/<name>/versions/1?api-version=v1` |
| Invoke the hosted agent | `POST /agents/<name>/endpoint/protocols/openai/responses?api-version=v1` |

### The live check

[`tests/live/test_scorecard_delivery_live.py`](../../tests/live/test_scorecard_delivery_live.py) passed against each of the three fixtures, one after the other, because the source it checks changed while this was verified:

| Fixture | The source that was checked | Result |
| --- | --- | --- |
| First | Managed scoring that made one run for a metric, and a delivery of three files | The seven checks it then had passed twice, the second time in 8 minutes 11 seconds |
| Second | Managed scoring that makes two runs for a metric and returns the result in parts | All eight checks passed, in 18 minutes 32 seconds |
| Third | The same, and a delivery that also writes `summary.md` | All eight checks passed, in 13 minutes 46 seconds |

Other work was merged into this source after the third run. The live check was not repeated for it. The default test run was. Each fixture was provisioned and torn down by hand, not by the check. What the check showed on the third fixture, and on the others as far as their source had it:

- **A customer-code run delivered its files.** Five Attempts by the live hosted agent, scored in this process. `evidence.json`, `report.html`, `scorecard.json` and `summary.md` were in the directory and nothing was written to the working directory. The report rendered the delivered scorecard value by value. `evidence.json` held the five Attempts with the response identifier the platform gave each. Outcome consistency was `0.5555555555555556` on all three fixtures, which is what the delivered evidence gives when calculated in the check: 2 of 2 successes gives 1, 2 of 3 gives one ninth, and the mean is five ninths.
- **An evidence limit was an unavailable measurement in both formats.** The Brier-based score in that run: see [Evidence limits](#evidence-limits).
- **A Foundry-managed run delivered the same files.** Five more Attempts by the hosted agent, with outcome consistency scored in Foundry. The scorecard was `complete`, with the value attributed to `foundry_managed` as `unverified`, in both formats. Foundry returned `0.5555555555555556` on all three fixtures, which is also what the delivered evidence gives. That is one value compared three times, not a fidelity rule.
- **The scorecard led to the runs, and the runs held what they were sent.** Read with the identifiers in the scorecard, the first run was `completed` with two rows, `value` and `size`, and the output item of the `value` row had the score in the scorecard. The run named in `encoded_result.run_ids` was `completed` with 49 rows, one for each part of an encoded result of 289 bytes. Every row of both runs held the same evidence, under the digest in the scorecard. Inflated, it held of each Attempt the identity, the Task, the execution status and the task outcome, and nothing else: the response identifiers and the resource measurements were in `evidence.json` only.
- **Reading a run took the customer's token.** Without a token, and with a token of the same identity for another resource (Azure Resource Manager), the read was answered with HTTP 401.
- **A reference was left on the evaluation and read back.** The request under [Leaving a reference in Foundry](#leaving-a-reference-in-foundry), with a synthetic location and the SHA-256 of the delivered `scorecard.json`. The evaluation returned both. The metadata of the run held only Foundry's own `trigger_type`, before and after.
- **A run made with metadata kept it after it failed.** A run of a test-only evaluator that raises, made by the check with `metadata` in the request, ended as `failed` and still returned the metadata. Foundry had added a key of its own, `trigger_type`.
- **A failed managed run delivered a scorecard marked partial.** Eight synthetic Attempts and a test-only evaluator that raises, as in the [live check of failed runs](../managed_scoring/readme.md#failed-and-partial-runs-observed-on-2026-11-27). `scorecard.json` and `report.html` were partial with the missing result named, `evidence.json` held all eight Attempts, and the metric scored in this process kept its result. In Foundry the run was `failed` with no output item, and still answered with the rows it was made with and the evidence in them, under the digest in the scorecard. A reference was left on its evaluation and read back.
- **An unavailable measurement that Foundry returned was delivered as one.** On the second and third fixtures. Two synthetic Attempts, one at each Task. The scorecard was `complete` with nothing missing. Outcome consistency was `unavailable`, attributed to `foundry_managed`, with the reason `no Task has at least two Attempts with a task outcome` and the two Tasks counted as excluded, in `scorecard.json` and in `report.html`.
- **A managed run that did not end in time delivered a scorecard marked partial.** With a time limit of 0 seconds the sweep asked for the cancellation of the first run while it was `queued`. The scorecard was partial with that as the execution error, and was delivered. The run was `canceled` when read afterwards, with no result.
- **A delivery that failed after a live managed run was an explicit reporting failure.** The check put a directory where `scorecard.json` goes. The sweep raised `ReportingFailure`, kept `evidence.json`, wrote `reporting-failure.json` and left no scorecard, no report and no summary. The failure carried the scorecard with the value that Foundry returned, which was the hand-calculated 0.68 to within 1e-12, and the run it names was still in the project as `completed`.

### Probes

Made by hand in the project of the first fixture, with test-only evaluators registered for the purpose. Not part of the live check. Each item was seen once unless it says otherwise. They are about the project and not about the source of the offering, so they were not repeated on the other fixtures.

- **Metadata was accepted on an evaluation and on a run when they were made**, and returned when they were read, also after the run had ended. Foundry also accepted and returned `properties` on both, and added properties of its own to them.
- **Changing the metadata of an evaluation merged it.** The keys it already had stayed, and the new key was added.
- **Nothing changed the metadata of a run.** `PATCH` on a run and on an evaluation was answered with HTTP 404. `POST` on a run with a metadata body was answered with HTTP 200 and the run was unchanged when read. Microsoft documents that request as the cancellation of a run. It was sent to a run that had completed.
- **The limits that were enforced were wider than the documented ones.** Seventeen and one hundred keys, a key of 65 characters and a value of 5,000 characters were accepted. A value of 100,000 characters was refused with a limit of 50,000, and a key of 1,000 characters with a limit of 250. A value that is an object was refused on a run. The refusals name a run-history service behind the project, which stores metadata as tags.
- **Evidence near the size limit.** Items of 900,000, 1,000,000 and 1,900,000 characters ran. A test-only evaluator that returns the length of what it is given was given every character each time. The run answered with the whole item each time. The output item held the whole item at 900,000 characters, and only its first 1,000 characters at 1,000,000 and at 1,900,000, with no field that says anything was left out.
- **A file with the purpose `evals` has to be JSONL.** `scorecard.json` and `report.html` were refused with `invalid filename: wrong suffix`. A `.jsonl` file was accepted and read back unchanged.
- **A file with the purpose `assistants` was accepted** for `scorecard.json` and for `report.html`, and read back unchanged. Such a file is a file of the project. Nothing ties it to a run, and nothing was found that shows it with one.
- **A run has a result asset that its creator was not given access to.** The run's properties name an asset under `/evaluationresults`. It points to blob storage in a storage account that is not in the resource group of the project. The three requests tried for credentials to it were answered with HTTP 400.
- **Deleting worked, and the answer said otherwise.** Deleting a run was answered with `"deleted": false`. The run and its output items were then answered with HTTP 404, and the evaluation and its other run were still read. Deleting the evaluation was answered the same way, and the evaluation and its remaining run were then answered with HTTP 404.
- **Reading a run took a role in the project.** After the signed-in user's two role assignments in the fixture were removed, Foundry User on the account and Foundry Project Manager on the project, the read of a run that had been answered until then was answered with HTTP 403 within three minutes: the identity `does not have permissions for Microsoft.CognitiveServices/accounts/AIServices/evaluations/read actions`. The identity could still manage the resource group through Azure Resource Manager.
- **Evaluations are listed with their metadata** by `GET /openai/v1/evals`.

### Documented, not observed

From the Microsoft REST reference of the Azure OpenAI Evals API v1 (page dated 2026-05-27, read on 2026-11-27):

- Metadata is a set of 16 key-value pairs. Keys are strings of at most 64 characters and values are strings of at most 512.
- `POST /openai/v1/evals/<eval>` updates the name and the metadata of an evaluation.
- `POST /openai/v1/evals/<eval>/runs/<run>` cancels a run. The reference lists no request that updates one. The offering cancels with the `/cancel` path above, which worked.

### Teardown

The resource group created for this work, `rg-are-54-d9d5d6de`, was deleted with everything registered in it, and a separate `az group exists` returned `false`. The soft-deleted Foundry account was purged.

## Not verified

- **A CI platform.** No sweep ran in a CI job, and no directory was published as a build artifact. Access to a published artifact, its retention and its deletion were not exercised on any CI platform.
- **What the Foundry portal shows** of a managed run, of its metadata or of a reference in it.
- **Which role is enough to read a run.** The identity had Foundry User on the account and Foundry Project Manager on the project, and was refused once it had neither. Neither was tried alone, and no second identity was used.
- **How long Foundry keeps an evaluation, a run or its output items**, and whether a project can be set to expire them.
- **Whether a managed run made by the SDK behaves the same.** Every request was made over REST.
- **Metadata near the documented limits on an evaluation after the fact.** The limits were probed when an evaluation and a run were made.
- **Any other way to put a file with a run**, such as a dataset of the project. Only the two file purposes and the result asset above were tried.
