# Agent reliability evaluators

Evaluators for the twelve reliability metrics of [Towards a Science of AI Agent Reliability, version 3](https://arxiv.org/html/2602.16666v3), for agents built on Azure Foundry. An Evaluator sweep makes the Attempts the Evaluators need and reports each metric individually in a reliability scorecard. There is no combined score and no built-in threshold.

Vocabulary is defined in [`GLOSSARY.md`](GLOSSARY.md). Decisions that are hard to reverse are recorded in [`docs/adr/`](docs/adr/).

## What exists today

Two metrics, end to end, scored in your own process:

| Metric | Documentation |
| --- | --- |
| Outcome consistency | [`docs/outcome_consistency/readme.md`](docs/outcome_consistency/readme.md) |
| Brier-based score | [`docs/brier_based_score/readme.md`](docs/brier_based_score/readme.md) |

The other ten metrics are listed in every scorecard as not configured. No Foundry agent target and no Foundry-managed scoring exists yet; the agent target is an interface that you implement.

When a selected metric uses confidence, the sweep asks the agent for a confidence rating in a separate request after each Attempt. How that works, and when a rating is unavailable, is described in the [Brier-based score documentation](docs/brier_based_score/readme.md#how-the-confidence-rating-is-collected).

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
)
print(to_json(scorecard))
```

An Evaluator can also be called on its own, without a sweep: see the metric's documentation.

## Where things are

The copyable source has one directory per module of the specification.

| Module | Path | Contents |
| --- | --- | --- |
| Evaluators | `src/reliability_evaluators/evaluators/` | The canonical metric definitions, one file per metric, the metric result contract (`result.py`), the catalogue of the twelve metrics (`catalogue.py`), the registry of Evaluators that exist (`registry.py`) and the confidence/outcome pairs the predictability metrics share (`confidence_pairs.py`). |
| Evidence model | `src/reliability_evaluators/evidence/` | The Attempt record, its validation, the confidence evidence of an Attempt (`confidence.py`), and the grouping of Attempts by Task. |
| Evaluator sweep | `src/reliability_evaluators/sweep/` | What a customer declares (`declaration.py`), the public entry point `run_sweep` (`runner.py`) and the confidence-only request that follows an Attempt (`confidence.py`). |
| Agent target | `src/reliability_evaluators/agent_target/` | The interface the sweep invokes an agent through, for an Attempt and for the confidence follow-up, with the target's declaration of whether that follow-up is isolated. |
| Scorecard | `src/reliability_evaluators/scorecard/` | Assembly of the machine-readable scorecard (JSON). |
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
- **Evaluator sweep seam** (`tests/sweep/`): `run_sweep` with the fake agent target in `tests/fakes.py`, asserting on the machine-readable scorecard only.
- **Live Foundry verification**: opt-in, marked `live`.

Expected values are calculated by hand, never by the code under test.
