# The human-readable report, and what is written where

An Evaluator sweep returns the reliability scorecard as JSON-compatible data. Give it a directory and it also writes the scorecard, a human-readable report of it and the attempt-level evidence there:

```python
from reliability_evaluators.scorecard import ReportingFailure
from reliability_evaluators.sweep import run_sweep

try:
    scorecard = run_sweep(
        tasks=tasks,
        agent_target=my_agent_target,
        metrics=["outcome_consistency"],
        artifact_dir="artifacts/reliability",
    )
except ReportingFailure as failure:
    print(failure)  # what could not be done, and what was kept
    raise
```

| File | Contents |
| --- | --- |
| `scorecard.json` | The machine-readable scorecard. This is what a pipeline reads. |
| `report.html` | The human-readable report, rendered from the scorecard. |
| `evidence.json` | The attempt-level evidence on its own: `{"schema_version": 1, "attempts": [...]}`, the same Attempts as in the scorecard. |
| `reporting-failure.json` | Only after a reporting failure. See [Reporting failure](#reporting-failure). |

Choose a directory in the run or artifact context of the platform the sweep runs on, such as a directory your CI publishes as a build artifact. The offering writes these files there and keeps nothing anywhere else. Access to them, their retention and their deletion are governed by that platform ([ADR 0003](../adr/0003-no-evidence-store.md)).

Without `artifact_dir` nothing is written. `render_report(scorecard)` in `reliability_evaluators.scorecard` returns the report as a string for a scorecard you already have.

## What the report shows

The report is one HTML file. It opens from disk or from a CI artifact view, with no server, no scripts and no external resources.

- **Each metric on its own.** All twelve metrics are listed, in the order of the scorecard, each with its state, its value or the reason there is none, its definition (version, what it measures, calculation, fidelity conventions, scale, interpretation), its counts and denominators, its scoring path, its fidelity, and the assessment of your acceptance criterion for it if you declared one. There is no combined score and no threshold of the offering's own.
- **States that cannot be mistaken for each other.** A metric shows the state the scorecard records for it, with its meaning next to it. Each state has its own colour and outline.

  | State in the scorecard | Shown with |
  | --- | --- |
  | `available` | Available: a value was validly calculated from the evidence. |
  | `unavailable` | Unavailable measurement: the metric could not be validly calculated from the evidence. No number is reported. |
  | `not_applicable` | Not applicable: the protocol of the metric does not apply to the declared experiment. Nothing was measured. |
  | `not_configured` | Not configured: the metric was not selected, so it was not measured. |
  | anything else | A note that the report has no description of this state, and the state as recorded. |

- **Execution failure apart from all of those.** How the running of an Attempt ended is its execution status. It is shown in the attempt-level evidence and is not a metric state. An Attempt that did not complete is marked, shows its execution error, and has a dash where the scorecard records no task outcome for it: it is not shown as a failed Task. A metric that left it out says so in its counts, with the reason. How the execution of a declared experiment ended, and why it stopped if it did, is shown with that experiment.
- **Declared experiments.** Every [controlled experiment](../sweep/controlled-experiments.md) in the scorecard is shown as the scorecard records it: its Task, agent configuration, starting-state policy, baseline and variation, the test resources it may change, and its execution with the Attempts planned and recorded.
- **Attempt-level evidence.** Every Attempt in the scorecard has its own block with everything the scorecard records for it: today its identity, Task, execution status, execution error, task outcome with its source, the ordered action evidence with its source, the resource measurements with their units and boundaries, the confidence evidence (the rating, its normalized value, the original response, or the reason there is no rating), the operational constraints that applied and the violation assessments with their evidence and source. An Attempt of an experiment also shows its experiment, its starting-state policy, the intended condition with its role, the observed condition with its source, and the customer controls that were invoked around it. Wherever a metric names an Attempt, in the evidence its value rests on, in the evidence it excluded or in a confidence/outcome pair, the identity links to that Attempt.

## A rendering of the scorecard, and nothing else

The report is produced from the scorecard's JSON text. It reads no other input.

- Every value in the report is a value of the scorecard, shown where the scorecard has it. Nothing is calculated, combined, averaged or rounded for display.
- Every value of the scorecard is in the report. A field that the report has no layout of its own for is laid out by its shape, as a group of fields, a table or a list. A field or a state that a later scorecard adds therefore appears without a change to the report.
- Each shown value carries, in the HTML attribute `data-scorecard`, the [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901) of that value in the scorecard. The tests use it to compare the report with the scorecard value by value, in both directions.

How values are written:

| In the scorecard | In the report |
| --- | --- |
| A string | The string, unchanged. Text from your evidence is shown as text and never interpreted as HTML. |
| A number | The number exactly as `scorecard.json` writes it. |
| `true`, `false` | `true`, `false` |
| `null` | A dash (—): no value is recorded. |
| An empty list or object | `none` |
| A field name | The name, unchanged. A name can be data, such as a tool name in an action-type mapping. |

### Precision

A number is shown with every digit the scorecard has for it. A Task value of one ninth is shown as `0.1111111111111111`, not as `0.11`. A very small or very large number is written with an exponent, as the JSON writes it: an elapsed time of under ten microseconds can read `7.70005863159895e-06` seconds.

The scorecard holds a number that is not whole as a double-precision floating-point value, and its JSON writes it as the shortest decimal text that reads back as exactly that value. That is the limit of the representation: a value that floating-point arithmetic cannot hold exactly is shown with the small error it was calculated with, the way the sum of 0.1 and 0.2 is `0.30000000000000004`. The report does not hide that by rounding.

## Reporting failure

When `artifact_dir` is given, the sweep either delivers all three files or raises `ReportingFailure`. It never returns as if it had delivered.

| What happened | What is raised | What is at the location |
| --- | --- | --- |
| The directory cannot be created or used | `ReportingFailure`, before any Attempt is made | Nothing from the offering |
| The declaration is rejected | `ReportingFailure`, caused by the `ValueError`, before any Attempt is made | `reporting-failure.json` |
| The sweep stops after Attempts were made, for example on a task-success rule that returns no task outcome | `ReportingFailure`, caused by the original error | `evidence.json` with the Attempts made until then, those of an experiment included, and `reporting-failure.json` |
| The scorecard was produced, and one of the files cannot be produced or written | `ReportingFailure`, caused by the original error | The files written before the one that failed, and `reporting-failure.json` |

- The files are written in the order evidence, scorecard, report, and writing stops at the first failure. A report is therefore never present without the scorecard it renders, and a reporting failure never leaves a report.
- When the directory is opened, an `evidence.json`, `scorecard.json`, `report.html` or `reporting-failure.json` that an earlier run left in it is removed. What the directory holds afterwards is from this run, so an old report cannot be read as the result of a run that failed.
- `reporting-failure.json` has `"status": "reporting_failed"`, what could not be done (`failure`), the error (`error`) and the names of the files that were kept (`retained`).
- The exception says the same in its message, in `retained`, and in the original error as its cause. It also carries `evidence`, the attempt-level evidence that existed, and `scorecard`, the scorecard if one was produced. When nothing could be written to the location, the exception is the only place they still are, and the caller can keep them another way.
- Evidence is written to the location the caller chose or handed back on the exception. It is kept nowhere else.

In CI, let `ReportingFailure` fail the job. A `reporting-failure.json` with no `report.html` next to it is a failed run, not a clean one.

## Assumptions

These are assumptions, not confirmed decisions.

- **The human-readable report is a self-contained HTML file** (specification assumption 19, with JSON as the machine-readable format). Self-contained means one file with its styles inline and with no scripts, no images, no fonts and no links to anything outside the file.
- The file names, the separate `evidence.json` and the content of `reporting-failure.json` are choices of this implementation. The specification asks only that the scorecard, the report and the evidence are written to a location the caller chooses.
- A rejected declaration is raised as `ReportingFailure` only when `artifact_dir` is given. Without it, `run_sweep` raises the `ValueError` or `EvidenceError` itself.

## What is not there yet

- A scorecard status of complete or partial and token usage are not in the scorecard yet. The report shows them once the scorecard records them, laid out by shape.
- The declared experiments, the conditions, the customer controls and the confidence evidence have no layout of their own in the report: they are laid out by shape. An experiment that stopped shows its status and the reason as text, without a marking of its own. An Attempt that did not complete is marked whatever its execution status is.
- Delivery has been exercised on a local file system only. Delivery into the artifact context of a Foundry-managed run, and whether a reference to the report can be attached to a Foundry run, are not verified.
