# Agent reliability evaluators

Evaluators for the twelve reliability metrics of [Towards a Science of AI Agent Reliability, version 3](https://arxiv.org/html/2602.16666v3), for agents built on Azure Foundry. An Evaluator sweep makes the Attempts the Evaluators need and reports each metric individually in a reliability scorecard. There is no combined score and no built-in threshold.

Vocabulary is defined in [`GLOSSARY.md`](GLOSSARY.md). Decisions that are hard to reverse are recorded in [`docs/adr/`](docs/adr/).

## What exists today

Ten metrics, end to end, scored in your own process:

| Metric | Documentation |
| --- | --- |
| Outcome consistency | [`docs/outcome_consistency/readme.md`](docs/outcome_consistency/readme.md) |
| Distributional trajectory consistency | [`docs/distributional_trajectory_consistency/readme.md`](docs/distributional_trajectory_consistency/readme.md) |
| Sequential trajectory consistency | [`docs/sequential_trajectory_consistency/readme.md`](docs/sequential_trajectory_consistency/readme.md) |
| Resource consistency | [`docs/resource_consistency/readme.md`](docs/resource_consistency/readme.md) |
| Fault robustness | [`docs/fault_robustness/readme.md`](docs/fault_robustness/readme.md) |
| Calibration | [`docs/calibration/readme.md`](docs/calibration/readme.md) |
| Strict confidence discrimination | [`docs/strict_confidence_discrimination/readme.md`](docs/strict_confidence_discrimination/readme.md) |
| Brier-based score | [`docs/brier_based_score/readme.md`](docs/brier_based_score/readme.md) |
| Constraint compliance | [`docs/constraint_compliance/readme.md`](docs/constraint_compliance/readme.md) |
| Conditional harm severity | [`docs/conditional_harm_severity/readme.md`](docs/conditional_harm_severity/readme.md) |

You can declare your own acceptance criteria per metric, and the scorecard reports each as met, not met, or not assessed with a reason: see [`docs/acceptance_criteria/readme.md`](docs/acceptance_criteria/readme.md).

When a selected metric uses confidence, the sweep asks the agent for a confidence rating in a separate request after each Attempt. How that works, and when a rating is unavailable, is described in the [Brier-based score documentation](docs/brier_based_score/readme.md#how-the-confidence-rating-is-collected). Calibration, strict confidence discrimination and the Brier-based score are calculated from the same ratings and reported as three separate results.

The Evaluator sweep can also run controlled experiments, with your own controls for preparing state, applying a condition, verifying what occurred and cleaning up: see [`docs/sweep/controlled-experiments.md`](docs/sweep/controlled-experiments.md). Fault robustness needs one: it compares Attempts under the baseline with Attempts under a fault that your controls apply and verify.

Several metrics can be selected in one sweep. Evaluators share Attempts only where that is valid, and the scorecard shows which Attempts each one used: see [`docs/sweep/several-evaluators.md`](docs/sweep/several-evaluators.md).

The other two metrics have no Evaluator yet. Every scorecard lists all twelve, and a metric that was not selected appears as not configured. No Foundry agent target and no Foundry-managed scoring exists yet; the agent target is an interface that you implement.

A task outcome can be supplied or corrected after the run. Recalculating from the earlier scorecard, which you supply, gives a new scorecard that uses the corrected outcome and marks the earlier result as superseded: see [`docs/sweep/late-and-corrected-outcomes.md`](docs/sweep/late-and-corrected-outcomes.md).

The sweep returns the machine-readable scorecard. Given a directory, it also writes the scorecard, a human-readable report rendered from it and the attempt-level evidence there, or raises an explicit reporting failure: see [`docs/report/readme.md`](docs/report/readme.md).

## Using it in your project

There is no published package. Copy the directory [`src/reliability_evaluators/`](src/reliability_evaluators/) into your project and record the commit or tag you copied it from.

- It needs Python 3.10 or later and nothing outside the standard library.
- Nothing else in this repository is needed at run time. `pyproject.toml`, `uv.lock` and `tests/` are development tooling.

```python
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import Task, run_sweep


def item_is_closed(attempt, response):
    closed = my_test_service.item_is_closed(attempt.attempt_id)
    return TaskOutcome(success=closed, source="final state of the to-do service")


scorecard = run_sweep(
    tasks=[Task(task_id="close", prompt="Close the item.", success_rule=item_is_closed, attempts=5)],
    agent_target=my_agent_target,
    metrics=["outcome_consistency"],
    artifact_dir="artifacts/reliability",  # optional: scorecard.json, report.html and evidence.json go here
)
print(to_json(scorecard))
```

`artifact_dir` is a directory you choose in the run or artifact context of your CI platform. With it, the sweep delivers all three files or raises `ReportingFailure`.

An Evaluator can also be called on its own, without a sweep: see the metric's documentation. The two trajectory metrics also need a declared source of action evidence and an action-type mapping, which have no default. Fault robustness needs an experiment that declares a fault.

Attempt counts and execution limits are yours to set per Task, in an experiment or without one. Timeouts, cancellations and retries stay visible and are never scored as task failures, concurrency is opt-in, and a sweep that stops early still returns a scorecard that says it is partial: see [`docs/sweep/execution-limits.md`](docs/sweep/execution-limits.md).

## Where things are

The copyable source has one directory per module of the specification.

| Module | Path | Contents |
| --- | --- | --- |
| Evaluators | `src/reliability_evaluators/evaluators/` | The canonical metric definitions, one file per metric, the metric result contract (`result.py`), the catalogue of the twelve metrics (`catalogue.py`), the registry of Evaluators that exist (`registry.py`), the confidence/outcome pairs the predictability metrics share (`confidence_pairs.py`) and the capped ratio of accuracies the robustness metrics share (`_robustness.py`). |
| Evidence model | `src/reliability_evaluators/evidence/` | The Attempt record with its task outcome, ordered action evidence (`actions.py`), resource measurements, confidence evidence (`confidence.py`) and what it records about its experiment (`experiment.py`), its validation, and the grouping of Attempts by Task. Task outcomes supplied or corrected after the run (`corrections.py`), and the strict reading of evidence that a customer supplies again (`reading.py`). |
| Evaluator sweep | `src/reliability_evaluators/sweep/` | What a customer declares, execution limits included (`declaration.py`), the public entry point `run_sweep` (`runner.py`), the customer controls invoked around each Attempt of an experiment (`lifecycle.py`), the limits and the cancellation at work (`execution.py`), the confidence-only request that follows an Attempt (`confidence.py`) the rule for which Evaluators share Attempts (`reuse.py`), and the public entry point `recalculate` for an earlier scorecard with late or corrected task outcomes (`recalculation.py`). |
| Agent target | `src/reliability_evaluators/agent_target/` | The interface the sweep invokes an agent through, for an Attempt and for the confidence follow-up, with the target's declaration of whether that follow-up is isolated. |
| Scorecard | `src/reliability_evaluators/scorecard/` | Assembly of the machine-readable scorecard (JSON, `assemble.py`), the assessment of customer acceptance criteria (`acceptance.py`), the human-readable report rendered from the scorecard (self-contained HTML, `report.py`), and their delivery with the evidence to a directory the caller chooses, with the reporting failure (`delivery.py`). |
| Foundry-managed scoring | not built yet | |

`evaluators/` and `evidence/` are pure: standard library only, no I/O, no network and no Foundry dependency. The Foundry-managed evaluators are to be derived from them, so they may import only what the Foundry code-evaluator sandbox provides.

Each Evaluator has its documentation, with a worked example and graphics, in `docs/<metric identity>/readme.md`.

## Development

Development tooling is managed with [uv](https://docs.astral.sh/uv/). Customers do not need it.

| | Command |
| --- | --- |
| Install | `uv sync` |
| Default test run | `uv run pytest` |
| Opt-in live Foundry checks | `uv run pytest -m live` |

The default test run excludes tests marked `live`. Live checks create billable Azure resources: they stand up the disposable fixture in [`live_fixture/`](live_fixture/README.md) (a to-do service, a Foundry prompt agent and a Foundry hosted agent) and tear it down again. The only live check so far verifies that fixture itself.

Tests are written at three seams only:

- **Evaluator seam** (`tests/evaluators/`): an Evaluator's public interface, called with grouped Attempt evidence.
- **Evaluator sweep seam** (`tests/sweep/`): `run_sweep` with the fake agent targets and the fake customer controls in `tests/fakes.py`, asserting on the machine-readable scorecard only. `recalculate` is driven with the scorecard of such a sweep, read back from its JSON, and asserted on the new scorecard or on the failure it raises. The human-readable report is tested only as a rendering of that scorecard: `tests/report_reader.py` reads a report back and compares it with the scorecard value by value. Delivery and reporting failure are asserted on what the caller finds in the directory it chose and on the failure it is given.
- **Live Foundry verification**: opt-in, marked `live`. The fixture these checks run on is test infrastructure, so it is also checked without Azure in `tests/fixture/`: its to-do service and its task-success rule run on the local machine, through the interface the live checks use.

Expected values are calculated by hand, never by the code under test.
