# Agent reliability evaluators

Evaluators for the twelve reliability metrics of [Towards a Science of AI Agent Reliability, version 3](https://arxiv.org/html/2602.16666v3), for agents built on Azure Foundry. An Evaluator sweep makes the Attempts the Evaluators need and reports each metric individually in a reliability scorecard. There is no combined score and no built-in threshold.

Vocabulary is defined in [`GLOSSARY.md`](GLOSSARY.md). Decisions that are hard to reverse are recorded in [`docs/adr/`](docs/adr/).

## What exists today

Seven metrics, end to end, scored in your own process:

| Metric | Documentation |
| --- | --- |
| Outcome consistency | [`docs/outcome_consistency/readme.md`](docs/outcome_consistency/readme.md) |
| Distributional trajectory consistency | [`docs/distributional_trajectory_consistency/readme.md`](docs/distributional_trajectory_consistency/readme.md) |
| Sequential trajectory consistency | [`docs/sequential_trajectory_consistency/readme.md`](docs/sequential_trajectory_consistency/readme.md) |
| Resource consistency | [`docs/resource_consistency/readme.md`](docs/resource_consistency/readme.md) |
| Brier-based score | [`docs/brier_based_score/readme.md`](docs/brier_based_score/readme.md) |
| Constraint compliance | [`docs/constraint_compliance/readme.md`](docs/constraint_compliance/readme.md) |
| Conditional harm severity | [`docs/conditional_harm_severity/readme.md`](docs/conditional_harm_severity/readme.md) |

You can declare your own acceptance criteria per metric, and the scorecard reports each as met, not met, or not assessed with a reason: see [`docs/acceptance_criteria/readme.md`](docs/acceptance_criteria/readme.md).

When a selected metric uses confidence, the sweep asks the agent for a confidence rating in a separate request after each Attempt. How that works, and when a rating is unavailable, is described in the [Brier-based score documentation](docs/brier_based_score/readme.md#how-the-confidence-rating-is-collected).

The Evaluator sweep can also run controlled experiments, with your own controls for preparing state, applying a condition, verifying what occurred and cleaning up: see [`docs/sweep/controlled-experiments.md`](docs/sweep/controlled-experiments.md).

The other five metrics have no Evaluator yet. Every scorecard lists all twelve, and a metric that was not selected appears as not configured. No Foundry agent target and no Foundry-managed scoring exists yet; the agent target is an interface that you implement.

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

An Evaluator can also be called on its own, without a sweep: see the metric's documentation. The two trajectory metrics also need a declared source of action evidence and an action-type mapping, which have no default.

Attempt counts and execution limits are yours to set per Task. Timeouts, cancellations and retries stay visible and are never scored as task failures, concurrency is opt-in, and a sweep that stops early still returns a scorecard that says it is partial: see [`docs/evaluator_sweep/execution-limits.md`](docs/evaluator_sweep/execution-limits.md).

## Where things are

The copyable source has one directory per module of the specification.

| Module | Path | Contents |
| --- | --- | --- |
| Evaluators | `src/reliability_evaluators/evaluators/` | The canonical metric definitions, one file per metric, the metric result contract (`result.py`), the catalogue of the twelve metrics (`catalogue.py`), the registry of Evaluators that exist (`registry.py`) and the confidence/outcome pairs the predictability metrics share (`confidence_pairs.py`). |
| Evidence model | `src/reliability_evaluators/evidence/` | The Attempt record with its task outcome, ordered action evidence (`actions.py`), resource measurements, confidence evidence (`confidence.py`) and what it records about its experiment (`experiment.py`), its validation, and the grouping of Attempts by Task. |
| Evaluator sweep | `src/reliability_evaluators/sweep/` | What a customer declares (`declaration.py`), the public entry point `run_sweep` (`runner.py`), the customer controls invoked around each Attempt of an experiment (`lifecycle.py`) and the confidence-only request that follows an Attempt (`confidence.py`). |
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

The default test run excludes tests marked `live`. Live checks need a disposable Foundry project and incur charges. None exists yet.

Tests are written at three seams only:

- **Evaluator seam** (`tests/evaluators/`): an Evaluator's public interface, called with grouped Attempt evidence.
- **Evaluator sweep seam** (`tests/sweep/`): `run_sweep` with the fake agent targets and the fake customer controls in `tests/fakes.py`, asserting on the machine-readable scorecard only. The human-readable report is tested only as a rendering of that scorecard: `tests/report_reader.py` reads a report back and compares it with the scorecard value by value. Delivery and reporting failure are asserted on what the caller finds in the directory it chose and on the failure it is given.
- **Live Foundry verification**: opt-in, marked `live`.

Expected values are calculated by hand, never by the code under test.
