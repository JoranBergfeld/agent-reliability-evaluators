# Foundry-managed scoring

Foundry-managed scoring runs an Evaluator's calculation as a custom evaluator in your own Foundry project, instead of in your own process. The Evaluator sweep still makes the Attempts and groups the evidence. Foundry only calculates.

Only **outcome consistency** can be scored this way in this version. Every other metric is scored in your own process.

A result that comes back from Foundry is checked when it is recovered. The number Foundry returns as the score is compared with the value in the encoded result that the evaluator returns with it. A result that passes is **authoritative**, and an acceptance criterion on it is assessed. A result that does not pass is an **unverified result**. It cannot be met or not met by an acceptance criterion. Read [the fidelity check](#the-fidelity-check) for what a passing check establishes and what it does not, before you choose this scoring path for a release gate.

A metric that is an **unavailable measurement** for the evidence comes back from Foundry as one, with its reason and its evidence counts: see [how a result comes back](#how-a-result-comes-back). The managed evaluator does not raise for it, and no run fails.

When Foundry gives no result for a metric, the sweep still returns a scorecard. It is partial, names the missing result with its execution error, and keeps the evidence and every other metric: see [when managed scoring gives no result](#when-managed-scoring-gives-no-result). You can then ask for the same evidence to be scored in your own process: see [a customer-process rescore](#a-customer-process-rescore). That never happens by itself.

| | |
| --- | --- |
| Chosen with | `ConfiguredEvaluator(metric_id=..., scoring_path="foundry_managed")` and `run_sweep(managed_scoring=...)` |
| Reported in | `metrics[].scoring_path`, `fidelity`, `limitation`, `managed_scoring` (with `managed_scoring.fidelity_check`) and `execution_failure` of the machine-readable scorecard, and `metrics[].customer_process_rescore` after a rescore |
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
3. It submits the grouped evidence of the metric to two Foundry evaluation runs of its own, one after the other, and waits for each to end. The first returns the plain value and the size of the result. The second returns the result itself, in parts.
4. It compares the plain value with the value in the result that came back in parts: [the fidelity check](#the-fidelity-check).
5. It reports the result Foundry returned, with the outcome of that check, or that Foundry returned none.

**Each metric has runs of its own.** An evaluator that raises fails its whole run, and takes the results of every other evaluator in that run with it (observed, see below). So no two metrics share a run: a metric whose run fails costs no other metric its result. The first runs of a sweep are all started before the first of them is waited for, and so are the second runs.

`FoundryManagedScoring` waits up to `run_timeout_seconds` (900 by default) for each run, counted from when that run was made, and asks for the run's status every `poll_interval_seconds` (10 by default). A run that has not ended in time is cancelled and its metric has no result. The second run takes two to three seconds for every six bytes of the result (observed): raise `run_timeout_seconds` for a sweep of several hundred Attempts.

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

A Foundry code evaluator is one Python source that defines `grade(sample, item)`, which returns a number. It cannot import the offering. `derive_artifact("outcome_consistency")` therefore builds that one source from the files your own process executes:

1. It starts from the canonical definition and from [`managed_scoring/grader.py`](../../src/reliability_evaluators/managed_scoring/grader.py), which reads the transported evidence with the Evidence model's own reader and encodes the result.
2. It follows their imports of other modules of the offering, and reads the source of each.
3. It joins those sources in import order. Every statement is kept as written. Only the imports between the joined modules, the module docstrings and `__all__` are left out, because one source does not need them.
4. It appends `grade`, which calls the canonical `evaluate` on the decoded evidence and returns the part of the result that the submitted item asks for.

The artifact is derived when it is needed and is never stored in the repository, so there is no generated file that could fall behind. The Evaluator-seam vectors of outcome consistency run against both the canonical definition and the derived artifact in [`tests/evaluators/test_outcome_consistency_managed_artifact.py`](../../tests/evaluators/test_outcome_consistency_managed_artifact.py). A change that made the two scoring paths give different values, or a different state, reason or counts for an unavailable measurement, fails there.

The derivation refuses, with a `DerivationError`, anything that would only fail later in Foundry:

| Refused | Why |
| --- | --- |
| An import of a module that the sandbox was not observed to accept | The sandbox has a fixed package list |
| A call named `compile`, `eval`, `exec`, `globals` or `__import__`, as a function or a method | The sandbox refuses such a source before running it. `re.compile` is refused too |
| Syntax newer than Python 3.12 | The sandbox ran Python 3.12 when observed |
| A source of 256 KB or more | The documented code-size limit. The artifact is about 80 KB |
| Two joined modules that define the same top-level name, an import under another name, an import of a module as an object, or an import inside a function | One joined source cannot keep them apart |

The canonical definitions and the Evidence model therefore have to stay inside those rules.

### Adding another Evaluator

Declare the Attempt evidence it is calculated from in `MANAGED_ATTEMPT_EVIDENCE` in [`artifact.py`](../../src/reliability_evaluators/managed_scoring/artifact.py), by the field names of the Attempt record. The same derivation then produces its artifact, and the same reader reads its evidence. Then run its Evaluator-seam vectors against the derived artifact, as the outcome consistency test does. That test is what shows the declared evidence is enough. Its result comes back through the same encoding, whatever units its counts are in and whatever its details hold, as long as they are JSON data. Only outcome consistency has been derived and proven. An Evaluator that also takes a customer declaration, such as the action types of the trajectory metrics, needs that declaration transported too, which nothing does yet.

## Registration

The evaluator is registered as `reliability-outcome-consistency`, of type `custom` with a `code` definition.

Registration is **idempotent**. Foundry itself is not: registering the same code twice creates a second version (observed). So the offering first lists the versions your project holds under that name and compares their code with the derived code:

- A version with exactly the derived code exists: it is used. Nothing is registered.
- None exists: a new version is registered.

The evaluation run then names that version explicitly. An evaluator of the same name with other code, whether older, newer or registered by someone else, is never run in its place. When you move your pin of the offering to a commit that changes the derived code, the next sweep registers a new version. Older versions stay in your project until you delete them.

The declared range of the score is the scale of the canonical definition. Only the plain value is in that range. The other numbers the evaluator returns are parts of an encoding, and Foundry does not enforce the range (observed). The desirable direction is declared as `neutral`, because the canonical definition does not say that higher is better.

## The evidence that is submitted

For each Attempt the evaluator is given: its identity, its Task, its execution status and its task outcome with its source. Nothing else about the Attempt is sent: its actions, its resource measurements, its confidence rating, its violation assessments and what it records about its experiment stay in your process. It goes to your own project.

Each Attempt is written exactly as the Attempt record writes itself in the scorecard, with the evidence that is not sent left empty, and is read back with the Evidence model's strict reader (`read_attempts`). That reader accepts an Attempt only when it is exactly what this version records.

The evidence travels as one string in one field, `outcome_consistency_evidence`: the SHA-256 digest of the evidence as JSON, a colon, and that JSON, deflated and written in base64.

- **A string, not structured data.** Structured numbers were altered on the way into a managed evaluator, and strings were not, in the earlier probes (decision ticket #12).
- **A digest.** The evaluator recomputes the digest before it reads anything. Evidence that changed on the way is rejected inside Foundry. It can never produce a value calculated from other evidence.
- **Deflated.** Every submitted row carries the evidence, because Foundry grades each row on its own, and a result takes many rows. The JSON is deflated with `zlib` (raw deflate, level 9), which the sandbox provides. In your project the stored evidence is therefore not readable as it stands: inflate the base64 text to read it.

The evaluator validates what it receives with the same code as your own process: the Evidence model rejects a task outcome that is not binary, an Attempt without an identity and two Attempts with the same identity, and the result contract rejects a value outside the scale. A field this version does not know is rejected, not ignored.

A run larger than 1,900,000 characters is not submitted, and the metric has no result. That is the size one run accepted when observed; larger ones were rejected, and the limit is on all the rows of a run together. The run for the parts of the result has one row for each part, each with the evidence, so the limit is reached sooner than the evidence alone would reach it: see [what it costs](#what-it-costs). Nothing is split or truncated. Batching is not designed.

## How a result comes back

A Foundry code evaluator can return one number for each submitted row and nothing else. Every other way of returning something was tried and failed the run: see [what was tried](#returning-a-result-that-is-not-a-plain-value-observed-on-2026-11-27). So a result comes back as numbers, one row a number. What else was considered is in [ADR 0006](../adr/0006-managed-results-come-back-as-encoded-parts.md).

Every submitted row carries the evidence and one more field, `result_part`, which says which part of the result that row asks for. The evaluator calculates the result on every row and returns the part that was asked for.

| `result_part` | What the evaluator returns |
| --- | --- |
| `value` | The metric value, as a plain number. For a metric that has no value: one below the minimum of its scale, which is `-1` for outcome consistency |
| `size` | The number of bytes of the encoded result |
| `1`, `2`, ... | Six bytes of the encoded result each, as a whole number. A part past the end is `0` |

The sweep makes two runs for a metric, under one evaluation:

1. The first run has two rows: `value` and `size`.
2. The second run has one row for each part, as many as the size says.

**The encoded result** is the result as the result contract writes it for the scorecard, without the metric definition: the metric identity, the state, the value, the reason, the counts with their reasons and identities, the details and the Attempts the value rests on. It is written as JSON, deflated, and put behind twelve bytes of digest.

- **Six bytes to a number.** A number of six bytes is a whole number below 2^48. A double holds it exactly, and fifteen significant digits write it exactly, so it does not depend on how the platform prints a double. Every such number that was returned came back unchanged (observed).
- **A digest.** The first twelve bytes are the start of the SHA-256 of the digest of the submitted evidence, a colon, and the deflated result. The sweep recomputes it from the evidence it submitted. A part that changed on the way back, a part of another result, or a result calculated from other evidence does not pass, and then nothing is reported.
- **Exact numbers.** Every number in the encoded result is written in the notation that reads back to the same double. The value the evaluator computed is among them, so it arrives exactly as it was computed, whatever becomes of the plain value. The sweep compares the two: [the fidelity check](#the-fidelity-check).
- **A strict reader.** The sweep accepts the result only when it is exactly what this version writes: the fields it knows, a state an Evaluator can return, a value on the scale of the metric or none, a count that matches the identities it names.

**The marker for no value never surfaces.** For a metric without a value the `value` row still has to return a number. It returns one below the scale. The sweep takes the state of the metric from the encoded result and never from that number. When the state says there is no value, the sweep checks that the number is the marker and reports no value; when the two disagree, nothing is reported. The numbers of the `size` row and of the parts are never reported as values either.

**The evaluator still raises for evidence that is not the evidence that was submitted,** as the canonical definition does in your own process for evidence it rejects. That is not an unavailable measurement: nothing was measured. Every row then fails, the run fails, and the metric has [no result](#when-managed-scoring-gives-no-result).

### What it costs

Foundry grades the rows of a run one after another, at two to three seconds a row (observed), and every row carries the evidence. So a result costs time and submitted characters in proportion to its size:

| Sweep | Size of the encoded result | Rows of the second run | What was seen or follows |
| --- | --- | --- | --- |
| 2 Attempts in 2 Tasks, an unavailable measurement | 259 bytes | 44 | Seen live: the sweep took 141 seconds, registration and both runs included |
| 8 Attempts in 2 Tasks, a value | 266 bytes | 45 | Seen live: the sweep took 164 and 203 seconds |
| 50 Attempts in 10 Tasks | 365 bytes | 61 | Not run live. Three to four minutes at the pace that was seen |
| 500 Attempts in 100 Tasks | 1,677 bytes | 280 | Not run live. Twelve to fifteen minutes at that pace for the second run alone, which is about the default `run_timeout_seconds`, and 1.4 million submitted characters |
| 750 Attempts in 150 Tasks | | 420 | Refused by the sweep: the run would submit 3.0 million characters |

The sizes of the last three rows were measured with the derived evaluator on this machine, with Attempt identities of the form `task-7/attempt-3`. Identities that have less in common deflate less. The limit of 1,900,000 characters is therefore reached somewhere between 500 and 750 such Attempts for outcome consistency, where the evidence alone would fit many more. A metric that reports more details has a larger result and reaches the limit sooner.

Before this version a managed sweep made one run with one row and took 43 to 52 seconds. Returning the state, the reason and the counts is what the added minutes buy.

**A row that Foundry does not grade is asked for again.** Foundry returned rows of a completed run as errors where the evaluator had not raised on them (observed: single rows of several runs, and once 148 of the 843 rows of a probe). A result takes many rows, so the sweep asks again for the parts that did not come back, in a further run with only those rows, up to three times in all. The evaluator returns the same part for the same evidence, and the digest is checked whichever run a part came from. A part that does not come back in three runs leaves the metric without a result. `managed_scoring.encoded_result.run_ids` names every run that returned a part.

## What the scorecard shows

The metric's entry has the same fields as a customer-process entry, and these differences:

| Field | Content |
| --- | --- |
| `state`, `reason` | What the evaluator returned in the encoded result: `available`, or `unavailable` with its reason |
| `value` | The number Foundry returned in the `value` row, or `null` for a metric without a value. It is never calculated in your own process, and it is never replaced by the value in the encoded result |
| `counts`, `details`, `evidence.attempt_ids` | What the evaluator returned in the encoded result. They are the same as on the other path |
| `scoring_path` | `foundry_managed` |
| `fidelity` | `authoritative` when the result passed [the fidelity check](#the-fidelity-check), `unverified` when it did not |
| `limitation` | Why the result is unverified, in words, with both numbers. Absent for an authoritative result |
| `attempts_used` | The Attempts the evaluator was given, as on the other path |
| `managed_scoring.evaluator` | The registered name, the version that ran and the SHA-256 of its code |
| `managed_scoring.eval_id`, `run_id`, `output_item_id` | Where the plain value came from in your project: the evaluation, its first run and the output item of the `value` row |
| `managed_scoring.encoded_result` | Where the rest came from: the `run_ids` of the runs that returned its parts, and the `bytes` and `parts` of the encoded result |
| `managed_scoring.fidelity_check` | The check the result was recovered with: its `outcome` (`passed` or `failed`), the `plain_value` Foundry returned, the `encoded_value` in the encoded result, and its `meaning` in words. Both values are `null` for a metric without a value |
| `managed_scoring.evidence_sha256` | The digest the evidence was submitted with: the SHA-256 of the evidence as JSON |
| `managed_scoring.platform_diagnostics` | The threshold, label and pass flag Foundry attached to the value. Absent for a metric without a value |
| `acceptance` | Your criterion assessed on the value, as on the other path, when the result is authoritative. `not_assessed` when the value is an unverified result or the metric is an unavailable measurement, with that as the reason |

**An unavailable measurement.** When the evaluator finds that the metric cannot be calculated from the evidence, the entry says so exactly as it does on the other path: `state` is `unavailable`, `value` is `null`, and `reason`, `counts`, `details` and `evidence` are what the evaluator returned. The scorecard is not partial because of it. It is `authoritative` when both channels agree that there is no value: see [a result without a value](#a-result-without-a-value). An unavailable measurement whose Attempts were [cut short](../sweep/execution-limits.md#metrics-in-a-partial-scorecard) is an execution failure, as on the other path.

```json
{
  "metric_id": "outcome_consistency",
  "state": "unavailable",
  "value": null,
  "reason": "no Task has at least two Attempts with a task outcome",
  "counts": {
    "tasks": {"total": 2, "valid": 0, "missing": [], "excluded": [{"reason": "fewer than two Attempts with a task outcome", "count": 2, "ids": ["close", "archive"]}]},
    "attempts": {"total": 2, "valid": 0, "missing": [], "excluded": [{"reason": "its Task has fewer than two Attempts with a task outcome", "count": 2, "ids": ["close/attempt-1", "archive/attempt-1"]}]}
  },
  "execution_failure": null,
  "scoring_path": "foundry_managed",
  "fidelity": "authoritative",
  "managed_scoring": {
    "evaluator": {"name": "reliability-outcome-consistency", "version": "1", "code_sha256": "6f2b..."},
    "eval_id": "eval_1",
    "run_id": "evalrun_1",
    "output_item_id": "1",
    "encoded_result": {"run_ids": ["evalrun_2"], "bytes": 259, "parts": 44},
    "fidelity_check": {
      "outcome": "passed",
      "plain_value": null,
      "encoded_value": null,
      "meaning": "Foundry returned the marker of a metric without a value as the score, and the encoded result, ... says that the metric has no value. ..."
    },
    "evidence_sha256": "dd62..."
  }
}
```

**Platform diagnostics.** Foundry attaches a threshold of 0.5 and a pass or fail label to every number an evaluator returns, whatever it stands for. For the value of a metric they are recorded under `platform_diagnostics` and nowhere else. They are not your acceptance criterion and not an assessment by the Evaluator. For the marker of a metric without a value, and for the size and the parts of the encoded result, they mean nothing and are not recorded. Foundry also did not enforce the declared score range when observed, which is why the value is checked against the scale when it is recovered.

In your Foundry project, the runs of this path therefore show many rows of large whole numbers with pass and fail labels. Only the `value` row of the first run is the metric, and for a metric without a value that row shows the marker.

The run's link to the Foundry portal is not recorded, because it embeds your subscription identifier. Find the run by its eval and run identifiers.

**What the scoring used** is not in the metric's entry. It is reported under `token_usage`, with the usage of the Attempts: what Foundry reported for each managed run of the Evaluator, or that it reported none. A run of a code evaluator was observed, in the earlier prototypes, to report an empty list of per-model usage and no usage on its result. That is reported as missing and not as zero. See [token usage](../token_usage/readme.md#scoring).

The human-readable report shows all of this, because it renders every field of the scorecard.

## The fidelity check

A managed result comes back on two channels. The check compares them, for one result at a time, when the result is recovered.

| Channel | What it is | What it carries |
| --- | --- | --- |
| Plain | The number Foundry returns as the score of the `value` row. It is what your Foundry project shows as the metric | The metric value, or the marker for a metric without one |
| Encoded | The encoded result: whole numbers of six bytes each, behind a digest that also covers the submitted evidence | The whole result: the state, the value exactly as the evaluator computed it, the reason, the counts, the details and the Attempts |

**The rule.**

- The plain value equals the value in the encoded result: the result is **authoritative**. Your acceptance criterion is assessed on it.
- The two differ: the result is an **unverified result**, with its limitation. Your acceptance criterion is not assessed.
- A result that was not checked is an unverified result too. Nothing in this version recovers a result without checking it. The rule is that only a check that passed makes a result authoritative.

The two numbers are compared exactly, as numbers. Nothing is rounded and no tolerance is used: two adjacent doubles are different values. A whole number that Foundry returns without a fraction, such as `1` for `1.0`, is the same number. So is a zero that comes back without its sign, which is the one difference the plain channel showed [when observed](#managed-output-fidelity-observed-on-2026-11-27).

The rule does not depend on the plain channel always preserving a value. It is applied to every result, so a value that the channel altered is found for that result.

### What a passing check establishes

- **The reported value is the one the evaluator returned in Foundry for the submitted evidence, unchanged on the way back.** The value reached the sweep twice, in two encodings that have nothing in common: once as a decimal number that Foundry wrote, and once as bytes that the evaluator wrote. They are the same double.
- **The encoded result is what the evaluator wrote for that evidence.** Its digest is recomputed from the evidence the sweep submitted. A part that changed on the way back, or a result calculated from other evidence, does not pass, and then there is [no result](#when-managed-scoring-gives-no-result).

### What it does not establish

- **That the calculation is right.** The check compares two outputs of the same evaluator. Both come from the same code and the same evidence, so an evaluator that calculated wrongly would return the same wrong value on both channels, and the check would pass. That the derived evaluator calculates what the canonical definition calculates is shown elsewhere: by running the Evaluator-seam vectors against the derived evaluator on a development machine ([how the evaluator is derived](#how-the-evaluator-is-derived)). That is not a check of what ran in Foundry.
- **That your own process would calculate the same value.** Nothing is calculated in your own process, for the check or for anything else on this path. The check never compares a managed value with a customer-process value, and a customer-process value never stands in for one. If you want that comparison, ask for a [customer-process rescore](#a-customer-process-rescore) and compare the two results yourself.
- **That Foundry ran the code that was registered.** The sweep names the evaluator version to run and records it. That the project ran that version is taken from the project.
- **Anything about the counts, the details, the reason or the state beyond their digest.** Only the value travels on both channels. Everything else is in the encoded result only. A metric that reports further numbers in its details has those on the encoded channel only.
- **Protection against deliberate change.** The digest is not a signature: it has no secret. It finds a part that was altered, lost or swapped on the way. Someone who can change what your project returns could also compute a matching digest. Your project's access control is what guards against that.
- **More than agreement between calls of the same evaluator.** Foundry grades each row in a call of its own, so the plain value and each part of the encoded result come from separate calls of the same evaluator on the same evidence. The canonical definitions are pure, so every call calculates the same result. If one did not, the two values would differ and the result would be an unverified result.

### When the two values differ

The result is an unverified result:

- `value` is the number Foundry returned in the `value` row. It is kept as a diagnostic.
- `fidelity` is `unverified`, and `limitation` names both numbers.
- `managed_scoring.fidelity_check` holds both: `plain_value` and `encoded_value`, with the outcome `failed`.
- `counts`, `details` and `evidence` are the ones in the encoded result. They belong to the value the encoded result holds, not to the reported one.
- An acceptance criterion on the metric is `not_assessed`, whichever of the two numbers would meet it.
- The scorecard is not partial because of it: a result came back.

The encoded value is not reported as the value in its place. Which of the two the evaluator computed is not established, and the number your Foundry project shows for the metric is the plain one. Reporting the other as authoritative would put a value in the scorecard that your project does not show.

```json
{
  "metric_id": "outcome_consistency",
  "state": "available",
  "value": 0.25,
  "scoring_path": "foundry_managed",
  "fidelity": "unverified",
  "limitation": "Foundry returned 0.25 as the value of this metric, and the encoded result that the Evaluator returned from Foundry with it holds 0.36. The two do not agree, so that the reported value equals the value the Evaluator computed there has not been established. ...",
  "managed_scoring": {
    "fidelity_check": {
      "outcome": "failed",
      "plain_value": 0.25,
      "encoded_value": 0.36,
      "meaning": "The number Foundry returned as the score differs from the value in the encoded result, ..."
    }
  },
  "acceptance": {"status": "not_assessed", "reason": "the value is an unverified result: its fidelity to the evaluator-computed value has not been established"}
}
```

A plain value that is not on the scale of the metric is not reported at all, and the metric then has [no result](#when-managed-scoring-gives-no-result). It is not a value of the metric, so it cannot be kept as one.

### A result without a value

An unavailable measurement has no value to compare. Its state, its reason and its counts travel on the encoded channel only. What the plain channel carries is the marker for a metric without a value.

- The marker came back, and the encoded result says the metric has no value: the two channels agree that there is none. The result is `authoritative`, and `fidelity_check` records the outcome `passed` with both values `null`.
- Anything else came back as the plain value: the two channels disagree on whether there is a value. There is [no result](#when-managed-scoring-gives-no-result).

`authoritative` here means that the absence of a value was confirmed on both channels, and that the reason and the counts are what the encoded result holds under its digest. No second channel carries the reason or the counts. An acceptance criterion on the metric is not assessed in either case, because the metric is an unavailable measurement.

## When managed scoring gives no result

A project that cannot be used stops the sweep before any Attempt is made, with `ManagedScoringFailure` or a `ValueError`. Nothing was collected, so there is nothing to keep.

After the Attempts, the sweep does not stop. It returns a scorecard that is **partial**:

- The metric has no result. Its `state`, `value` and `fidelity` are `null`. It is not reported as an unavailable measurement, as a value of zero or as a task failure: nothing was measured, and no task outcome is changed.
- `execution_failure.reason` says that Foundry-managed scoring gave no result, followed by the execution error. `managed_scoring.error` holds the execution error on its own, next to how far the scoring got: the evaluator, the evaluation, the run that was made last and the digest of the submitted evidence, each `null` if it was not reached.
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
    "evaluator": {"name": "reliability-outcome-consistency", "version": "1", "code_sha256": "6f2b..."},
    "eval_id": "eval_1",
    "run_id": "evalrun_1",
    "output_item_id": null,
    "evidence_sha256": "dd62...",
    "error": "the Foundry-managed run evalrun_1 of eval_1 ended as failed: UserError: All examples failed due to invalid user input; ..."
  }
}
```

`execution_failure` is the field a metric without a result always has. Its `experiment_ids` and `task_ids` are empty here, because no Attempt was cut short: it is the scoring that failed. A metric whose [Attempts were cut short](../sweep/execution-limits.md#metrics-in-a-partial-scorecard) has no `managed_scoring.error`.

With `artifact_dir`, the partial scorecard, its report and the evidence are delivered as for any sweep.

| Situation | What you get |
| --- | --- |
| The project cannot be reached or refuses your identity when the evaluator is registered | The sweep stops before any Attempt is made |
| The metric has no Foundry-managed evaluator, or `managed_scoring` was not given | `ValueError` before any Attempt is made |
| The metric is an unavailable measurement for the evidence, for example no Task has two Attempts | **A result**, not a failure: `state` is `unavailable`, with its reason and counts. See [what the scorecard shows](#what-the-scorecard-shows) |
| The evidence was altered on the way | No result. The evaluator raises on every row, and Foundry then fails the run |
| A run would be larger than one run accepts: the evidence is too large, or the result has too many parts | No result. That run was not submitted |
| A part of the encoded result did not come back as the evaluator returned it, or the size did not | No result. The digest does not match, or a part is not a number of six bytes. Nothing of what came back is reported |
| The plain value and the encoded result disagree on whether the metric has a value | No result |
| The plain value is on the scale of the metric and differs from the value in the encoded result | **A result**, not a failure: an unverified result with both numbers. See [when the two values differ](#when-the-two-values-differ) |
| A run returned fewer output items than rows were submitted, or not one for each row | No result |
| The project refuses or does not answer a request after the Attempts were made | No result, with the request and its answer |
| The run ended as failed or canceled, or did not end in time | No result, with the run's error |
| A completed run with an item that carries an error, that has no result for the metric, or whose score is not a number | No result. One such row is enough: a result is not put together from some of its parts |
| A completed run with a row whose result is in the status `error` | The part that row asked for is asked for again. No result when it has not come back after three runs |
| A score outside the scale of the metric | No result. The score is not reported, whatever the encoded result holds |
| Managed scoring stops on an error of its own | No result for every metric on this path, with that error |

A sweep whose Attempts were cut short by an execution limit or a cancellation, with too little collected to calculate from, has no managed failure either. The evaluator returns an unavailable measurement, and the scorecard reports the metric as it does on the other path: without a result, [because its Attempts were cut short](../sweep/execution-limits.md#metrics-in-a-partial-scorecard).

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
| `metrics` | The metrics to rescore, by metric identity. At least one. Each has to be a metric that the scorecard attributes to Foundry-managed scoring, with a result or with a failure. Give the same `ConfiguredEvaluator` as the sweep when the Evaluator has declarations of its own. |
| `action_evidence` | Declared again as for the sweep, when a rescored metric needs it. |
| `acceptance_criteria` | Declared again as for the sweep. Required for a rescored metric that the earlier scorecard assessed a criterion for. |

### What the new scorecard holds

The new scorecard is the earlier one with one addition per rescored metric: `customer_process_rescore`, inside the entry of that metric. The [token usage](../token_usage/readme.md#recalculation-and-rescore) of that Evaluator gains an entry of the same name, which says that the rescore used no tokens. Nothing else changes. The example is shortened.

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

- **The managed result stays.** The entry of the metric still holds what Foundry-managed scoring gave: the value with its fidelity, and with its limitation when it is an unverified result, the unavailable measurement with its reason and counts, or the failure with its execution error. `metrics[].value` is never the rescored value, and `metrics[].fidelity` is not changed by a rescore: an unverified result stays unverified, whatever the rescore gives.
- **A partial scorecard stays partial.** A managed result that is missing is still listed in `missing`. The rescore does not make up for it.
- **The rescore is a result of its own.** It has every field that a metric scored in your own process has, calculated by the same Evaluator from the Attempts the managed evaluator was given: its state, value, definition, counts, details and the Attempts the value rests on. It is attributed to `customer_process` and is `authoritative` for that path. It can be an unavailable measurement, with its reason, or have an execution failure of its own when the Attempts were cut short.
- **Acceptance is assessed separately.** `metrics[].acceptance` is the assessment of the managed result and is not changed: an authoritative value keeps its verdict, and an unverified result, an unavailable measurement or a missing result is still `not_assessed`. Your criterion is also assessed on the rescore, in `customer_process_rescore.acceptance`. Which of the two your release gate reads is your decision. Reading the rescore means deciding on a value from your own process, not on the one Foundry returned.
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

Each sweep creates one evaluation and two runs in your project for every metric on this path, more runs when a part is asked for again, and evaluator versions as described above. Every row of every run holds the submitted evidence. The offering does not delete them. Your project governs access to them, their retention and their deletion.

## Assumptions

The first comes from the specification and is not a confirmed decision. It is implemented as written. The others are choices made here, where the specification left the question open.

1. **The managed artifact is a Foundry code evaluator, not an endpoint evaluator** (specification assumption 17). An endpoint evaluator would need the offering to host an endpoint that receives your data. A code evaluator limits the canonical definitions to the code-evaluator sandbox. The `skipped` output with a reason that Microsoft documents belongs to endpoint evaluators and is not used.
2. **A managed result is authoritative when its two channels agree, and for no other reason.** The specification asks for a fidelity check and does not say what it compares. The check compares the plain value with the value in the encoded result, exactly. It uses nothing that was calculated in your own process, because a customer-process calculation may not stand in for a managed one. What that agreement establishes, and what it does not, is in [the fidelity check](#the-fidelity-check). See [ADR 0007](../adr/0007-a-managed-result-is-authoritative-when-its-two-channels-agree.md).
3. **The plain value stays the reported value, also when the check fails.** The encoded result carries the value too. When the two differ, the scorecard reports the number of the `value` row as an unverified result and shows the encoded one beside it, in `managed_scoring.fidelity_check`. It does not report the encoded value as the value, although the encoded channel is the one behind a digest: the plain number is what your Foundry project shows for the metric, and which of the two the evaluator computed is not established.
4. **A result without a value is authoritative when both channels agree that there is none.** `fidelity` says whether a value is known to equal what the evaluator computed, and an unavailable measurement has no value. What can be checked is checked: that the plain channel returned the marker for no value where the encoded result says there is none. Its reason and its counts travel in the encoded result only, under its digest, as the counts of a value do. It is an unavailable measurement and never an unverified result: its `state` says so, and an acceptance criterion on it is not assessed for that reason.
5. **A plain value that is not on the scale of the metric leaves the metric without a result.** It could be reported as an unverified result like any other value that differs. It is not, because it is not a value of the metric: the result contract does not hold it.
6. **The sign of a zero is not compared.** Foundry returned negative zero as `0` when observed. The two are the same number, and the check compares numbers. No metric of this version distinguishes them.
7. **Every row of the same run is graded by the same build of the sandbox.** The parts of a result are put together from rows that are graded separately. If two rows deflated the same result differently, the digest would not match and the metric would have no result. The one live comparison of the sandbox's deflate with this machine's gave the same bytes.

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

The live check ([`tests/live/test_managed_outcome_consistency.py`](../../tests/live/test_managed_outcome_consistency.py)) passed three times, in three projects. The last time it provisioned its own project and ran the derived evaluator as it was then: 70,736 bytes, SHA-256 `809f7a6c1d02c24e9792a316f22f8534bdc0522fbdf6e623e141c88f86164b9d`. What it showed each time:

- **A sweep scored outcome consistency in Foundry.** Eight synthetic Attempts on the fixture's to-do service, four successes and one failure at one Task and three failures at the other. Foundry returned `0.6799999999999999`, where the hand-calculated value is 0.68. The scorecard attributed it to `foundry_managed` as `unverified`. Foundry labelled it `pass` against its threshold of 0.5. The sweep took 43 to 52 seconds, registration and the run included.
- **Registration was idempotent and made as the signed-in identity.** Registering twice more left one version with the derived code. Foundry recorded the signed-in user as its creator.
- **An unavailable measurement was an explicit failure.** With one Attempt at each Task the run ended as failed with `UserError: All examples failed due to invalid user input; please check the individual sample outputs for details.` and no output item. The sweep raised `ManagedScoringFailure`, as it did at that time. An unavailable measurement now comes back as one: see [returning a result that is not a plain value](#returning-a-result-that-is-not-a-plain-value-observed-on-2026-11-27).

A fourth run did not get as far as the check. The project it had just created answered its first request with HTTP 404 `Project not found`, the provisioning stopped and the resource group was torn down. The provisioning now waits for the project to answer, and the run after that passed.

The code-evaluator sandbox:

- **It ran Python 3.12.3.** Read with the `platform` module. `import sys` is refused.
- **Accepted modules:** `__future__`, `bisect`, `collections`, `dataclasses` (with `kw_only`), `enum`, `fractions`, `hashlib`, `itertools`, `json`, `math`, `platform`, `re`, `typing`. That covered everything the canonical definitions and the Evidence model import. More were accepted [later](#returning-a-result-that-is-not-a-plain-value-observed-on-2026-11-27).
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
- **An evaluator that raises on one of two items leaves the run `completed`.** The item it raised on was `completed` with a result in the status `error`, a `null` score, label and pass flag, and the error on the result's own `sample.error`: `ProviderGradingError: An error occurred during grading (provider run ...; item 1)`. The item's own `sample.error` was `null`. `result_counts.errored` was 1. The other item had its score. The same held with a second evaluator in the run, which kept both of its results. The offering then submitted one item per run. It now submits one row for each part of a result, and asks again for a part whose row comes back like this.
- **Runs made at the same time all ended.** Five runs started back to back all ended within 133 seconds of the first being made. Twelve runs of the evaluator that returns a number, started back to back, all ended as `completed` within 201 seconds of the first being made, of which making the twelve took 135 seconds with a new token for every request. No request was refused. Twelve derived evaluators with real evidence were not tried.

The live check ([`tests/live/test_managed_scoring_failures.py`](../../tests/live/test_managed_scoring_failures.py)) passed against that project, with the derived evaluator as it was then (SHA-256 `809f7a6c1d02c24e9792a316f22f8534bdc0522fbdf6e623e141c88f86164b9d`). It makes a managed run fail without touching the canonical definition: a test-only evaluator that raises is registered under a name of its own, and the project is told to run it where the sweep names the derived one. What it showed:

- **A failed managed run left a partial scorecard.** Eight synthetic Attempts, as in the check above. The run ended as `failed` with the `UserError` above. The scorecard was `partial`, outcome consistency had no state and no value, `missing` named it with that error, all eight task outcomes were kept, and resource consistency, scored in this process from the same Attempts, kept its value. The scorecard, its report and the evidence were delivered to a directory.
- **A rescore on request stood beside the failure.** Rescoring the delivered scorecard gave `0.6799999999999999`, attributed to `customer_process`, from evidence with the digest that was submitted to the project. The managed failure and the partial status were unchanged.
- **A rescore stood beside an unverified value.** With the derived evaluator left to run, Foundry returned `0.6799999999999999` and the rescore gave `0.6799999999999999`. This is one comparison of one value. It is not a fidelity rule, and the managed value stays unverified.
- **Of two managed runs of one sweep, one failed and the other kept its result.** For this check only, a second Evaluator was put on the managed path and the test-only evaluator was run in its place. Its run ended as `failed`, once with the `UserError` and once with the `SystemError` above. The run of outcome consistency, made at the same time, returned `0.6799999999999999` both times, and the scorecard kept it.
- **The earlier live check still passed** against the same project, with its last case changed: an unavailable measurement then left a partial scorecard where the sweep used to raise. It now [comes back as one](#returning-a-result-that-is-not-a-plain-value-observed-on-2026-11-27).
- **Timing.** The three checks took 109 seconds together against a project that already existed, and 215 seconds together with the three of the earlier live check.
- **Teardown.** The resource group created for this work, `rg-are-50-0ee7446d`, was deleted with everything registered in it, and a separate `az group exists` returned `false`. The soft-deleted Foundry account was purged.

### Returning a result that is not a plain value, observed on 2026-11-27

Observed in Sweden Central in one more disposable Foundry project with no model deployment, with Azure CLI 2.93.0, signed in as a user, and with the same interfaces as above: `api-version=v1` with the header `Foundry-Features: Evaluations=V1Preview` to register, and the `/openai/v1/evals` routes for evaluations, runs and output items. Each item was seen once unless it says otherwise. The probes used test-only code evaluators registered in that project for the purpose.

**What a code evaluator can return.** Only a number. Microsoft documents a float between 0.0 and 1.0; nothing else is documented for code evaluators.

| The evaluator returned | What came back |
| --- | --- |
| A float | The score. `reason` is `null`. No other field holds anything the evaluator computed |
| A dict: `score` and `reason`; `result` and `reason`; `result`, `result_reason` and `result_result`; `score`, `label`, `reason`, `passed`, `status` and `properties` | Run `failed`, `UserError: All examples failed due to invalid user input; please check the individual sample outputs for details.`, no output item. All four |
| A string holding a number, a string holding JSON, a tuple, a list, `True`, `None` | The same failure, each |
| A float, after `print` of a marker text | The score. The printed text was nowhere in the output item or the run |
| A float, with two metrics declared in the evaluator definition | One score, as with one metric. Returning a dict with a key for each metric failed the run as above |
| `True`, with the metric declared as `boolean` | The same failure |
| `3`, with the metric declared as `ordinal` from 0 to 5 | Run `failed`, `SystemError: ... pass_threshold must be between 0.0 and 1.0` |
| NaN, infinity | Run `failed`, `SystemError: Evaluation system error: (InternalError) AOAI evaluation run ... failed with status failed`, no output item. Each |
| `-3.5`, with a declared range of 0 to 1 | `-3.5`, in a completed run. The declared range is not enforced |
| The integer `10**400`, which no double holds | Run `completed`, one item, its result in the status `error` with `ProviderGradingError` |

- **The evaluator is not given anything but the submitted row.** `sample` was an empty dict, and `item` held the submitted fields and nothing else. Initialization parameters of the criterion did not reach it.
- **A criterion cannot hand the evaluator another field under the same name.** A flat data schema with a `data_mapping` worked for one criterion. Several criteria of one run that map the same name from different fields were refused when the run was made: `Saved code grader 'part-1' maps 'part' from ${data.part_1} while grader 'part-0' maps it from ${data.part_0}; every grader receives the same uploaded item, so a mapped name has one source across the graders of a run.` A literal in a mapping was refused with HTTP 400. So the parts of a result cannot be asked for by several criteria over one row, which would have submitted the evidence once.

**Numbers.** An evaluator returned 44 numbers, 34 of them in one run.

- **Every finite double but one came back as the same double**: 21 of the 22 that were tried, among them `0.1`, `1/3`, `0.6799999999999999`, `0.49999999999999994`, `0.8300000000000001`, `5e-324`, `1.7976931348623157e308` and `1e22`. The one is negative zero, which came back as `0`, without its sign.
- **They are printed in another notation.** A whole number has no fraction (`1.0` as `1`, `float(2**53)` as `9007199254740992`), and large and small ones have an exponent (`1E+22`, `1E-07`, `5E-324`).
- **Integers up to 2^53 came back exactly**, all 14 that were tried, among them `2**48 - 1` and `2**53 - 1`.
- **An integer is turned into a double.** `2**53 + 1` came back as `9007199254740992`, and `10**30 + 7` as `1E+30`. Seven integers above 2^53 were tried, and each came back as the double nearest to it.

**Rows.**

- **Each row is graded in a fresh interpreter.** A module-level counter was 1 on each of 20 rows.
- **About two seconds a row with a trivial evaluator, one row after another**, in runs that were made alone or beside a few others: 6 rows in 29 to 40 seconds, 21 rows in 68, 60 rows in 134, 100 rows in 243. A run of 1,000 rows had not ended after 25 minutes and was cancelled. With the derived evaluator: 2 rows and then 45 rows in 164 and 203 seconds, registration included.
- **The size limit is on the run, not on a row.** Ten rows of 100,000 characters ran. Thirty such rows, and three rows of 1,000,000 characters, were refused with HTTP 413 when the run was made. The body of that answer names the subscription identifier.
- **The byte channel worked.** A test-only evaluator returned 119 bytes, a digest and a deflated text, as 20 numbers of up to six bytes, and all 20 came back unchanged. The sandbox's `zlib` deflated that text to the same bytes as this machine's.
- **Accepted modules, in addition to the list above:** `zlib`, `base64`, `binascii`, `struct`, `gzip`, `bz2`, `lzma`, `array`, `decimal`, `string`, `functools`, `operator`, `statistics`, `textwrap`, `copy`, `numbers`, `abc`, `datetime`, `time`, `random`, `heapq`. The derived evaluator uses `zlib` and `base64` of these.

**Rows whose evaluator raises, and rows that are not graded.**

- **Every row raises: the run fails**, with the `UserError` above and no output item. Seen with one row and with two.
- **One row of several raises: the run is `completed` and returns every item.** Seen five times, with 3 and 6 rows and the raising row first, in the middle and last. The item of the raising row has a result in the status `error` with `ProviderGradingError`, as recorded above. The exception text is not returned.
- **A row whose evaluator did not raise was returned as an error, twice.** In a probe with the rows `ok`, `raise`, `ok`, the first row came back as an error beside the one that raised. In a sweep with the derived evaluator, one of the 44 rows of the second run came back as an error, and none of the 44 raises: `ProviderGradingError: An error occurred during grading (provider run ...; item 19)`. The sweep of that time reported no result for the metric. It now asks again for such a part.

**The live check** ([`tests/live/test_managed_outcome_consistency.py`](../../tests/live/test_managed_outcome_consistency.py)) passed against that project, with the derived evaluator of this commit: 79,599 bytes, SHA-256 `6f2b6af38023da79ac849f12cd24f088c533837a73fa8c4c88e1e21ccd64acb5`. It was run twice. The first time one of its four cases failed, on the row that was not graded. What it showed:

- **An unavailable measurement came back with its reason and counts.** Two synthetic Attempts, one at each of two Tasks. No run failed. The first run returned `-1` as the plain value and `259` as the size. The second returned 44 parts. The scorecard was `complete` and showed `unavailable`, the reason `no Task has at least two Attempts with a task outcome`, and the two excluded Tasks and the two excluded Attempts by identity, as the customer-process path shows them. The marker was not in the scorecard. Seen once in full, and once up to the row that was not graded.
- **A value came back with its counts and details.** Eight synthetic Attempts, as in the checks above. The plain value was `0.6799999999999999`, the encoded result 266 bytes in 45 parts, and the scorecard showed the counts, the per-Task details and the eight Attempts the value rests on. Seen twice.
- **A run that mixes an available and an unavailable result returned every item.** One evaluation was handed two sets of evidence in the same runs: five Attempts at one Task, and one Attempt at one Task. The first run had four rows and returned `0.36` and `228` for the one, `-1` and `248` for the other. The second run had 80 rows, 38 for the one result and 42 for the other, and returned all 80 as `completed`. Read back, the one result was `available` with `0.36`, the other `unavailable` with its reason and its excluded Attempt. Seen twice.
- **Registration was idempotent**, as before. Seen twice.
- **The check of failed runs and of the rescore still passed** ([`tests/live/test_managed_scoring_failures.py`](../../tests/live/test_managed_scoring_failures.py)), with this commit's two runs for a metric, once.
- **Timing.** The four cases took 582 and 672 seconds against a project that already existed.
- **Teardown.** The resource group created for this work, `rg-are-48-a35ff320`, was deleted with everything registered in it, and a separate `az group exists` returned `false`. The soft-deleted Foundry account was purged.

### Managed-output fidelity, observed on 2026-11-27

Observed in Sweden Central in one more disposable Foundry project with no model deployment, with Azure CLI 2.93.0, signed in as a user, and with the same interfaces as above: `api-version=v1` with the header `Foundry-Features: Evaluations=V1Preview` to register an evaluator, and the `/openai/v1/evals` routes for evaluations, runs and output items. The answers were read over REST with Python's `json` module, which reads a decimal number as the double nearest to it. Each item was seen twice unless it says otherwise.

**Documented.** Microsoft documents that a code-based evaluator "returns a float score between 0.0 and 1.0", with the metric type `continuous` ([Other custom evaluators (preview)](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/other-custom-evaluators), dated 2026-11-11, read on 2026-11-27). It documents nothing about the precision or the notation in which that float is kept or returned. It also documents that when `grade` raises or times out, "the service records that item's result as `0.0` and marks it as an error". That is not what was observed: such a row came back with a `null` score, in the status `error`. Everything below is observation, not documented behaviour.

**The probe.** A test-only code evaluator ([`tests/live/test_managed_fidelity.py`](../../tests/live/test_managed_fidelity.py)) computes a double from text and returns it on the channel that a row asks for. Text reached the sandbox unchanged in the earlier probes, so the text says which double is computed.

| Channel | What the evaluator returns | Compared with |
| --- | --- | --- |
| Numeric | The double itself, as the score. This is how the plain value of a metric comes back | What the encoded channel returned for the same text |
| Encoded | The eight bytes of the double as two whole numbers, of six bytes and of two. This is how the parts of an encoded result come back | The bytes of the double that this machine computes from the same text |

It was asked for 281 computations, which give 271 different doubles, each on both channels: 843 rows. 71 of those doubles take 17 significant digits to write.

- 49 decimals as text. Among them the values of the earlier evidence (`0.83`, `0.8300000000000001`, `0.6799999999999999`, `0.49999999999999994`, `0.49999999999999906`), decimals longer than a double holds, numbers small and large enough to be written with an exponent (from `5e-324` and `0.0000001` to `1e22` and `1.7976931348623157e308`), whole numbers, zero with either sign and negative numbers.
- 14 neighbours: the doubles directly below and above `0.83`, `0.68`, `0.5`, `1.0`, `0.1`, `0.36` and `0.3333333333333333`.
- 38 ratios that the evaluator divides itself: every k/n for n of 3, 7, 9, 11 and 13.
- 4 means that the evaluator adds and divides itself, such as the mean of `0.1`, `0.2` and `0.3`.
- 120 doubles between 0 and 1, and 15 more each with the double directly above it, chosen with a fixed seed.
- 26 doubles of any size and sign, chosen with the same seed.

**What came back.**

- **The encoded channel returned every double exactly.** All 562 whole numbers were the bytes of the double that this machine computes from the same text. So the sandbox also computed the same double as this machine in every case, the ratios and the means included.
- **The numeric channel returned every double as the same number.** All 281 scores were equal to the double the encoded channel carried.
- **One came back as another double: negative zero, as `0`.** The other 280 were the same double bit for bit. No metric of this version distinguishes the two zeros.
- **Adjacent values stayed apart.** `0.83` came back as `0.83` and `0.8300000000000001` as `0.8300000000000001`, and so did each of the 14 neighbours and the 15 pairs.
- **The alteration of structured inputs was not seen in outputs.** Decision ticket #12 saw `0.83` arrive in an evaluator as `0.8300000000000001` and `0.49999999999999994` as `0.49999999999999906` when they were submitted as numbers. Returned as scores, all four came back as themselves. Inputs were not probed again: the evidence is submitted as text.
- **Whole numbers of up to six bytes came back unchanged as scores.** 28 of them, among them 0, 2^47, 2^48 - 2, 2^48 - 1 and every power of ten up to 10^14. Seen three times.

**The derived evaluator.** The same was asked of the evaluator derived from outcome consistency, with its real encoding, for eight sets of synthetic evidence whose value is a long decimal. The expected values are fractions calculated by hand. Seen once.

| Evidence | By hand | Plain value | Value in the encoded result |
| --- | --- | --- | --- |
| 2 successes in 3 Attempts | 1/9 | `0.1111111111111111` | `0.1111111111111111` |
| 5 in 7 | 9/49 | `0.1836734693877551` | `0.1836734693877551` |
| 7 in 9 | 25/81 | `0.30864197530864196` | `0.30864197530864196` |
| 9 in 11 | 49/121 | `0.4049586776859504` | `0.4049586776859504` |
| 7 in 13 | 1/169 | `0.005917159763313609` | `0.005917159763313609` |
| Two Tasks: 2 in 3, 5 in 7 | 65/441 | `0.14739229024943312` | `0.14739229024943312` |
| Three Tasks: 2 in 3, 5 in 7, 4 in 5 | 7219/33075 | `0.2182615268329554` | `0.2182615268329554` |
| Two Tasks: 4 in 5, 0 in 3 | 0.68 | `0.6799999999999999` | `0.6799999999999999` |

The eight encoded results were 219 to 303 bytes and came back in 345 parts. Each result passed its digest.

**The live checks**, with the fidelity check in place and the derived evaluator unchanged (79,599 bytes, SHA-256 `6f2b6af38023da79ac849f12cd24f088c533837a73fa8c4c88e1e21ccd64acb5`):

- **A sweep reported an authoritative value, and an acceptance criterion was assessed on it.** Fifteen synthetic Attempts in three Tasks. Foundry returned `0.2182615268329554`, the encoded result held the same, `fidelity` was `authoritative`, and the criterion `value at most 0.25` was `met`, assessed by the offering. Seen twice.
- **The earlier live checks passed with the new expectations**: [`tests/live/test_managed_outcome_consistency.py`](../../tests/live/test_managed_outcome_consistency.py) once, and [`tests/live/test_managed_scoring_failures.py`](../../tests/live/test_managed_scoring_failures.py) twice. The value `0.6799999999999999` was `authoritative`, with the same number on both channels, in five sweeps. The unavailable measurement was `authoritative` too: the plain channel returned the marker, and `fidelity_check` recorded `passed` with no values. A rescore still stood beside the managed value as a result of its own.
- **No live result failed its check.** A value that differs between the channels was produced with the fake project only.

**Rows that were not graded.** They matter here because a result takes many rows.

- **A sweep asked again for a part, live, and recovered the result.** In one of the sweeps above, one of the 45 rows of the second run came back as an error. The sweep asked for that part in a third run of one row, and the result passed its digest and its fidelity check. `encoded_result.run_ids` named both runs. Seen once. Until now this was exercised against the fake project only.
- **148 of 843 rows of one probe were not graded.** The probe was run twice, each time as nine runs of up to 100 rows that were started back to back. The first time every row was graded. The second time, while the sweeps of the live checks were running in the same project, six of the nine runs returned 6 to 36 of their rows as errors: result status `error`, `ProviderGradingError`, a `null` score, and the item itself `completed` without an error, as recorded above. The probe evaluator raises on none of these rows. Asked for again, all 148 were graded. Whether the other runs in the project were the cause is not known.
- **5 of the 345 part rows of the derived evaluator were not graded**, in one of four runs. Asked for again, all five were graded.
- **Timing.** The 843 rows of the probe took 472 seconds when every row was graded, and 846 seconds with the 148 asked for again. Runs that are started back to back are graded side by side.

**Teardown.** TEARDOWN_PLACEHOLDER

## Not verified

- **That the plain channel preserves every double.** It returned 271 different doubles as the same number in one project on one day, and 21 more in the earlier probe. That is a sample, and Microsoft documents nothing about it. The fidelity check is applied to every result and does not rest on it.
- **A plain value that differs from the encoded one, in a live project.** Every live result passed its check. The mismatch, and the unverified result it leaves, were exercised against the fake project only.
- **The answers read by anything but Python's `json` module over REST.** Another reader, or the Azure SDK, may read a decimal number differently. The offering reads them this way.
- **Another region, another day, or a project with a model deployment.** One project in Sweden Central was probed.
- **Asking again more than once for a part, and giving up after three runs**, against a live project. A sweep asked again once, live, and recovered its result. The rest was exercised against the fake project only.
- **How often Foundry does not grade a row, and why.** Seen in single rows of a few runs, in no row of a probe of 843 rows, and in 148 rows of the same probe when other runs were in progress in the project. Three runs for a part may not be enough when it happens that often: that was not seen, and not tried.
- **A second run of more than 100 rows,** and a result near the size limit of a run. The largest second run of a sweep had 54 rows, and the largest run of a live check or a probe 100.
- **Rows of one run graded by different builds of the sandbox**, which could deflate the same result differently.
- **A metric that is not applicable.** The encoded result carries any state an Evaluator can return. Outcome consistency returns only `available` and `unavailable`, so no other state was returned through it.
- **An Evaluator other than outcome consistency in Foundry.** Resource consistency was derived and scored through the same encoding against the fake project, in the tests of failed runs. It was not run in a live project.
- **Sign-in as a service principal or a federated workload identity**, and a token function built on `azure-identity`. Only a user signed in to the Azure CLI was used.
- **Which role is needed.** The identity had Foundry User on the account and Foundry Project Manager on the project. Neither was tried alone.
- **Token renewal.** A new token is asked for every five minutes and once more after an HTTP 401. No run was long enough to need it.
- **The sandbox limits.** Microsoft documents no network access, code under 256 KB, 120 seconds per call and 2 GB of memory. Only the refused `socket` and `sys` imports were seen. The size limit was not approached.
- **Twelve derived evaluators, each in a run of its own.** Twelve runs at once were seen only with a test-only evaluator and a one-word item. Whether a project limits how many runs it accepts at once is not known. A run that is refused leaves its metric without a result and the others as they are.
- **Evidence near the size limit.**
- **A managed run that does not end in time.** The cancellation of such a run was exercised against the fake project only.
- **A completed run whose only item has a result in the status `error`.** Seen only in runs with several items, and once with an integer that no double holds.
- **A request that the live project refuses after the Attempts were made**, such as a rate limit. Exercised against the fake project only.
- **Two pipelines registering at the same moment.** Both could register a version with the same code. Either is the derived code, so the result would be the same, but the project would hold a duplicate.
- **What the Foundry portal shows** for the evaluator, the run or its result.
- **What a managed run reports of the usage of its scoring.** The saved runs of code evaluators in the earlier prototypes reported none. What a run of a derived evaluator reports, and how per-model usage rows relate to each other, was not observed here.
