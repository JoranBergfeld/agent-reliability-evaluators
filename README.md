# Agent reliability evaluators

Evaluators for the twelve reliability metrics of [Towards a Science of AI Agent Reliability, version 3](https://arxiv.org/html/2602.16666v3), for agents built on Azure Foundry. An Evaluator sweep makes the Attempts the Evaluators need and reports each metric individually in a reliability scorecard. There is no combined score and no built-in threshold.

Vocabulary is defined in [`GLOSSARY.md`](GLOSSARY.md). Decisions that are hard to reverse are recorded in [`docs/adr/`](docs/adr/).

## What exists today

All twelve metrics, end to end, scored in your own process. One of them, outcome consistency, can also be scored in your Foundry project:

| Metric | Documentation |
| --- | --- |
| Outcome consistency | [`docs/outcome_consistency/readme.md`](docs/outcome_consistency/readme.md) |
| Distributional trajectory consistency | [`docs/distributional_trajectory_consistency/readme.md`](docs/distributional_trajectory_consistency/readme.md) |
| Sequential trajectory consistency | [`docs/sequential_trajectory_consistency/readme.md`](docs/sequential_trajectory_consistency/readme.md) |
| Resource consistency | [`docs/resource_consistency/readme.md`](docs/resource_consistency/readme.md) |
| Fault robustness | [`docs/fault_robustness/readme.md`](docs/fault_robustness/readme.md) |
| Environment robustness | [`docs/environment_robustness/readme.md`](docs/environment_robustness/readme.md) |
| Prompt robustness | [`docs/prompt_robustness/readme.md`](docs/prompt_robustness/readme.md) |
| Calibration | [`docs/calibration/readme.md`](docs/calibration/readme.md) |
| Strict confidence discrimination | [`docs/strict_confidence_discrimination/readme.md`](docs/strict_confidence_discrimination/readme.md) |
| Brier-based score | [`docs/brier_based_score/readme.md`](docs/brier_based_score/readme.md) |
| Constraint compliance | [`docs/constraint_compliance/readme.md`](docs/constraint_compliance/readme.md) |
| Conditional harm severity | [`docs/conditional_harm_severity/readme.md`](docs/conditional_harm_severity/readme.md) |

You can declare your own acceptance criteria per metric, and the scorecard reports each as met, not met, or not assessed with a reason: see [`docs/acceptance_criteria/readme.md`](docs/acceptance_criteria/readme.md).

When a selected metric uses confidence, the sweep asks the agent for a confidence rating in a separate request after each Attempt. How that works, and when a rating is unavailable, is described in the [Brier-based score documentation](docs/brier_based_score/readme.md#how-the-confidence-rating-is-collected). Calibration, strict confidence discrimination and the Brier-based score are calculated from the same ratings and reported as three separate results.

The Evaluator sweep can also run controlled experiments, with your own controls for preparing state, applying a condition, verifying what occurred and cleaning up: see [`docs/sweep/controlled-experiments.md`](docs/sweep/controlled-experiments.md). The three robustness metrics each need one, with a variation of their own kind. Fault robustness and environment robustness compare Attempts under the baseline with Attempts under a fault, or under an equivalent presentation of the same tools and data, that your controls apply and verify. Prompt robustness compares them with Attempts under the prompt variants that you supply or approve. The offering generates no variation.

Several metrics can be selected in one sweep. Evaluators share Attempts only where that is valid, and the scorecard shows which Attempts each one used: see [`docs/sweep/several-evaluators.md`](docs/sweep/several-evaluators.md).

Outcome consistency can be scored as a custom evaluator in your own Foundry project instead of in your own process. The evaluator is derived from the same definition, registered under your identity, and its value is reported as an unverified result, which cannot be met or not met by an acceptance criterion. An unavailable measurement comes back from Foundry as one, with its reason and evidence counts. When Foundry gives no result, the scorecard is partial and names the missing result with its execution error, and nothing is scored in your own process instead. You can ask for a customer-process rescore of the same evidence, which is reported beside the managed result and never replaces it: see [`docs/managed_scoring/readme.md`](docs/managed_scoring/readme.md). The other metrics are scored in your own process only.

Every scorecard lists all twelve, and a metric that was not selected appears as not configured. The sweep can make its Attempts against a Foundry prompt agent, or against a Foundry hosted agent that serves the Responses protocol, from your own code or CI: see [`docs/foundry_prompt_agent/readme.md`](docs/foundry_prompt_agent/readme.md) and [`docs/foundry_hosted_agent/readme.md`](docs/foundry_hosted_agent/readme.md). It can also invoke either agent through Foundry cloud evaluation, one evaluation run per Attempt, with repetition, grouping and your controls still the sweep's: see [`docs/foundry_cloud_evaluation/readme.md`](docs/foundry_cloud_evaluation/readme.md). Each Attempt records its entry point. For any other agent, the agent target is an interface that you implement.

A task outcome can be supplied or corrected after the run. Recalculating from the earlier scorecard, which you supply, gives a new scorecard that uses the corrected outcome and marks the earlier result as superseded: see [`docs/sweep/late-and-corrected-outcomes.md`](docs/sweep/late-and-corrected-outcomes.md).

The sweep returns the machine-readable scorecard. Given a directory, it also writes the scorecard, a human-readable report rendered from it, the text of a CI job summary and the attempt-level evidence there, or raises an explicit reporting failure: see [`docs/report/readme.md`](docs/report/readme.md). The files go to that directory on both scoring paths. What Foundry holds of a managed run, and how to leave a reference to the scorecard there, is in [`docs/report/delivery-alongside-foundry.md`](docs/report/delivery-alongside-foundry.md).

After a run, the scorecard reports the token usage that was measured, for each Attempt, for each Evaluator and for the sweep, with overlaps and gaps stated. It is metadata: it is not a metric and takes no part in acceptance. There is no estimate, no currency and no cap. The same usage is in the report and in the summary text that your pipeline publishes, and a standalone Evaluator reports it without a scorecard: see [`docs/token_usage/readme.md`](docs/token_usage/readme.md).

## Using it in your project

There is no published package. Copy the directory [`src/reliability_evaluators/`](src/reliability_evaluators/) into your project and record the commit or tag you copied it from.

- It needs Python 3.10 or later and nothing outside the standard library. Foundry-managed scoring and the Foundry agent targets also need a bearer token for your project, by default from the Azure CLI.
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
    artifact_dir="artifacts/reliability",  # optional: scorecard.json, report.html, summary.md and evidence.json go here
)
print(to_json(scorecard))
```

`artifact_dir` is a directory you choose in the run or artifact context of your CI platform. With it, the sweep delivers all four files or raises `ReportingFailure`.

An Evaluator can also be called on its own, without a sweep: see the metric's documentation. The two trajectory metrics also need a declared source of action evidence and an action-type mapping, which have no default. Each robustness metric needs an experiment that declares a variation of its kind: a fault, an environment variation, or a prompt variation with its prompt variants.

Attempt counts and execution limits are yours to set per Task, in an experiment or without one. Timeouts, cancellations and retries stay visible and are never scored as task failures, concurrency is opt-in, and a sweep that stops early still returns a scorecard that says it is partial: see [`docs/sweep/execution-limits.md`](docs/sweep/execution-limits.md).

## Where things are

The copyable source has one directory per module of the specification.

| Module | Path | Contents |
| --- | --- | --- |
| Evaluators | `src/reliability_evaluators/evaluators/` | The canonical metric definitions, one file per metric, the metric result contract (`result.py`), the catalogue of the twelve metrics (`catalogue.py`), the registry of Evaluators that exist (`registry.py`), the confidence/outcome pairs the predictability metrics share (`confidence_pairs.py`) and the capped ratio of accuracies the robustness metrics share (`_robustness.py`). |
| Evidence model | `src/reliability_evaluators/evidence/` | The Attempt record with its task outcome, ordered action evidence (`actions.py`), resource measurements, confidence evidence (`confidence.py`) and what it records about its experiment, including the prompt variant it ran under (`experiment.py`), its validation, and the grouping of Attempts by Task. Task outcomes supplied or corrected after the run (`corrections.py`), and the strict reading of evidence that a customer supplies again (`reading.py`). |
| Evaluator sweep | `src/reliability_evaluators/sweep/` | What a customer declares, execution limits included (`declaration.py`), the public entry point `run_sweep` (`runner.py`), the customer controls invoked around each Attempt of an experiment (`lifecycle.py`), the limits and the cancellation at work (`execution.py`), the confidence-only request that follows an Attempt (`confidence.py`) the rule for which Evaluators share Attempts (`reuse.py`), the public entry point `recalculate` for an earlier scorecard with late or corrected task outcomes (`recalculation.py`), and the public entry point `rescore_in_customer_process` for a customer-process rescore of the evidence of a Foundry-managed result (`rescore.py`). |
| Agent target | `src/reliability_evaluators/agent_target/` | The interface the sweep invokes an agent through, for an Attempt and for the confidence follow-up, with the target's declaration of whether that follow-up is isolated and what it recorded of each request (`interface.py`), and the targets for a Foundry prompt agent (`foundry_prompt_agent.py`) and a Foundry hosted agent (`foundry_hosted.py`) invoked from customer code, and for either one invoked through Foundry cloud evaluation (`foundry_cloud_evaluation.py`). |
| Scorecard | `src/reliability_evaluators/scorecard/` | Assembly of the machine-readable scorecard (JSON, `assemble.py`), the assessment of customer acceptance criteria (`acceptance.py`), the token usage of the Attempts, the Evaluators and the sweep, with what a customer declares for generated variations (`usage.py`), the human-readable report rendered from the scorecard (self-contained HTML, `report.py`), the text of a CI job summary rendered from it (Markdown, `summary.py`), and their delivery with the evidence to a directory the caller chooses, with the reporting failure (`delivery.py`). |
| Foundry-managed scoring | `src/reliability_evaluators/managed_scoring/` | The derivation of the Foundry evaluator from a canonical definition (`artifact.py`), what that evaluator runs around the definition, the evidence encoding it reads and the result encoding it returns (`grader.py`), your Foundry project reached as your own identity (`foundry.py`), and registration, submission and recovery of the result (`scoring.py`). Outcome consistency only. |

`evaluators/` and `evidence/` are pure: standard library only, no I/O, no network and no Foundry dependency. So are `managed_scoring/grader.py` and `scorecard/usage.py`. `agent_target/foundry_prompt_agent.py` and `agent_target/foundry_hosted.py` talk to Foundry, over plain REST with the standard library: see [ADR 0005](docs/adr/0005-foundry-targets-use-the-standard-library.md). `agent_target/foundry_cloud_evaluation.py` talks to Foundry through the `FoundryProject` of `managed_scoring/foundry.py`. The Foundry-managed evaluators are derived from their source, so they may use only what the Foundry code-evaluator sandbox accepts: [the list](docs/managed_scoring/readme.md#how-the-evaluator-is-derived) names the modules, and the calls it refuses, `re.compile` among them.

Each Evaluator has its documentation, with a worked example and graphics, in `docs/<metric identity>/readme.md`.

## Development

Development tooling is managed with [uv](https://docs.astral.sh/uv/). Customers do not need it.

| | Command |
| --- | --- |
| Install | `uv sync` |
| Default test run | `uv run pytest` |
| Opt-in live Foundry checks | `uv run pytest -m live` |

The default test run excludes tests marked `live`. Live checks create billable Azure resources and tear them down again. The check of the fixture itself stands up the disposable fixture in [`live_fixture/`](live_fixture/README.md) (a to-do service, a Foundry prompt agent and a Foundry hosted agent). The check of the Foundry hosted agent target (`tests/live/test_foundry_hosted_agent_live.py`) stands up the same fixture, runs a sweep against its hosted agent and compares outcome consistency with an independent calculation. The check of the Foundry prompt agent target (`tests/live/test_foundry_prompt_agent_live.py`) does the same against the fixture's prompt agent. The check of Foundry cloud evaluation as the entry point (`tests/live/test_foundry_cloud_evaluation_live.py`) runs one sweep per agent type on the same fixture, each Attempt a cloud evaluation run. The check of Foundry-managed scoring (`tests/live/test_managed_outcome_consistency.py`) stands up only an empty Foundry project.

Tests are written at three seams only:

- **Evaluator seam** (`tests/evaluators/`): an Evaluator's public interface, called with grouped Attempt evidence. The token usage that a standalone Evaluator reports is called the same way, with the same evidence. The same vectors are run against the Foundry evaluator derived from it, which `tests/managed_artifact.py` runs on this machine the way Foundry calls it.
- **Evaluator sweep seam** (`tests/sweep/`): `run_sweep` with the fake agent targets and the fake customer controls in `tests/fakes.py`, and for Foundry-managed scoring the fake Foundry project in `tests/fake_foundry.py`, asserting on the machine-readable scorecard only. The Foundry hosted agent target is driven the same way, against a fake Foundry project on the local machine (`tests/fake_foundry_hosted.py`) whose hosted agent works on the fixture's to-do service. So is the Foundry prompt agent target, against `tests/fake_foundry_prompt_agent.py`, with the fixture's Task declared for a sweep in `tests/todo_fixture_sweep.py`. The Foundry cloud evaluation target is driven against a fake project in the test process (`tests/fake_foundry_cloud_evaluation.py`), whose prompt agent and hosted agent work on the same to-do service. `recalculate` and `rescore_in_customer_process` are driven with the scorecard of such a sweep, read back from its JSON, and asserted on the new scorecard or on the failure they raise. The human-readable report is tested only as a rendering of that scorecard: `tests/report_reader.py` reads a report back and compares it with the scorecard value by value. The CI job summary is tested as a rendering of it too. Delivery and reporting failure are asserted on what the caller finds in the directory it chose and on the failure it is given.
- **Live Foundry verification**: opt-in, marked `live`. The fixture these checks run on is test infrastructure, so it is also checked without Azure in `tests/fixture/`: its to-do service and its task-success rule run on the local machine, through the interface the live checks use.

Expected values are calculated by hand, never by the code under test.
