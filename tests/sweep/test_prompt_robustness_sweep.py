"""Evaluator sweep seam: prompt robustness, from declared experiments to the reliability scorecard.

The sweep is driven through its public entry point with a fake agent target and fake
customer controls. Every assertion is on the machine-readable scorecard, or on what the
fake agent was given. Expected values are calculated by hand.

The Task is to add one item to a to-do list, and it succeeds when the list then holds
exactly one item. The fake agent adds its item only when the text it is given says "add",
in any case. Given any other wording it does nothing. It answers "Done." whatever happened.
"""

import json

import pytest

from reliability_evaluators.agent_target import AgentResponse
from reliability_evaluators.evidence import OutcomeCorrection, TaskOutcome
from reliability_evaluators.scorecard import render_report, to_json
from reliability_evaluators.sweep import (
    Condition,
    ExecutionLimits,
    Experiment,
    PromptVariant,
    RecalculationFailed,
    Task,
    recalculate,
    run_sweep,
)
from tests.fakes import FakeControls, FakeTodoService, todo_list
from tests.report_reader import assert_faithful_rendering

OUTCOME_SOURCE = "final state of the fake to-do service"
REQUEST_LOG = "request log of the fake to-do service"
PROMPT = "Add one item to the list."
ORIGINAL = Condition(condition_id="original", description="The agent is given the Task's own instruction.")
REWORDED = Condition(
    condition_id="reworded", kind="prompt", description="The agent is given the instruction in another wording."
)
SUPPORT_TEAM = "written by the support team"
# The agent understands the first two wordings and not the third.
PLEASE = PromptVariant(variant_id="please", text="Please add an item to my list.", source=SUPPORT_TEAM)
SHOUTED = PromptVariant(variant_id="shouted", text="  pls ADD 1 thing\n", source="taken from a support ticket")
TERSE = PromptVariant(variant_id="terse", text="One more item.", source=SUPPORT_TEAM)


class WordingSensitiveAgent:
    """A to-do agent that adds one item to its Task's list when the text it is given says "add".

    It gives up when the service refuses the item. Scripted by Attempt identity with
    "forgets", it does nothing whatever it is given, and with "unreachable", the invocation
    fails. `prompts` keeps the exact prompt of every invocation, by Attempt identity.
    """

    def __init__(self, service, script=None):
        self._service = service
        self._script = dict(script or {})
        self.prompts = {}

    def invoke(self, request):
        self.prompts[request.attempt_id] = request.prompt
        if self._script.get(request.attempt_id) == "unreachable":
            raise ConnectionError("the agent endpoint returned 503")
        if "add" in request.prompt.lower() and self._script.get(request.attempt_id) != "forgets":
            try:
                self._service.add(todo_list(request.task_id), "buy milk")
            except ConnectionError:
                pass
        return AgentResponse(output="Done.")


class Fixture:
    """A fake to-do service with a wording-sensitive agent, the controls and the experiments that use them."""

    def __init__(self, script=None, *, task_ids=("add",), controls=FakeControls):
        self.service = FakeTodoService(task_ids)
        self.agent = WordingSensitiveAgent(self.service, script)
        self.controls = controls(self.service)
        # The prompt of every Attempt the task-success rule was asked to judge, by Attempt identity.
        self.judged = {}

    def exactly_one_item(self, attempt, response):
        self.judged[attempt.attempt_id] = attempt.prompt
        items = self.service.lists.get(todo_list(attempt.task_id), [])
        return TaskOutcome(success=len(items) == 1, source=OUTCOME_SOURCE)

    def experiment(self, task_id="add", *, attempts, limits=ExecutionLimits(), **declared):
        task = Task(
            task_id=task_id, prompt=PROMPT, success_rule=self.exactly_one_item, attempts=attempts, limits=limits
        )
        return Experiment(
            **{
                "experiment_id": f"{task_id}-item",
                "task": task,
                "agent_configuration": {"agent": "todo-agent", "version": "3"},
                "starting_state": "fresh",
                "baseline": ORIGINAL,
                "variation": REWORDED,
                "prompt_variants": [PLEASE, TERSE],
                "controls": self.controls,
                "write_capable": True,
                "authorized_resources": [todo_list(task_id)],
                **declared,
            }
        )

    def sweep(self, *experiments, metrics=("prompt_robustness",), **declared):
        return run_sweep(experiments=experiments, agent_target=self.agent, metrics=metrics, **declared)


def metric(scorecard, metric_id="prompt_robustness"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def attempt(scorecard, attempt_id):
    (entry,) = [entry for entry in scorecard["attempts"] if entry["attempt_id"] == attempt_id]
    return entry


def test_a_sweep_scores_prompt_robustness_in_the_scorecard():
    # Baseline: the agent adds its item in both Attempts: 2/2 = 1.
    # Two Attempts under each prompt variant. The agent understands "please": 2/2. It does not
    # understand "terse" and does nothing: 0/2. Together 2/4 = 0.5.
    # 0.5 / 1 = 0.5.
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=2))

    prompt_robustness = metric(scorecard)
    assert prompt_robustness["state"] == "available"
    assert prompt_robustness["value"] == 0.5
    assert prompt_robustness["scoring_path"] == "customer_process"
    assert prompt_robustness["fidelity"] == "authoritative"
    assert prompt_robustness["definition"]["version"] == "1.0.0"
    nothing_left_out = {"missing": [], "excluded": []}
    assert prompt_robustness["counts"] == {
        "tasks": {"total": 1, "valid": 1, **nothing_left_out},
        "baseline_attempts": {"total": 2, "valid": 2, **nothing_left_out},
        "prompt_attempts": {"total": 4, "valid": 4, **nothing_left_out},
        "other_attempts": {"total": 0, "valid": 0, **nothing_left_out},
    }
    assert prompt_robustness["details"] == {
        "variation_kind": "prompt",
        "baseline": {
            "condition_ids": ["original"],
            "observed_from": [REQUEST_LOG],
            "tasks_with_a_valid_attempt": 1,
            "compared_tasks": 1,
            "compared_attempts": 2,
            "successes": 2,
            "accuracy": 1.0,
        },
        "prompt": {
            "condition_ids": ["reworded"],
            "observed_from": [REQUEST_LOG],
            "tasks_with_a_valid_attempt": 1,
            "compared_tasks": 1,
            "compared_attempts": 4,
            "successes": 2,
            "accuracy": 0.5,
        },
        "accuracy_ratio": 0.5,
        "capped": False,
        "per_task": [
            {
                "task_id": "add",
                "baseline": {"attempts": 2, "successes": 2, "success_frequency": 1.0},
                "prompt": {
                    "attempts": 4,
                    "successes": 2,
                    "success_frequency": 0.5,
                    "variants": [
                        {"variant_id": "please", "attempts": 2, "successes": 2, "success_frequency": 1.0},
                        {"variant_id": "terse", "attempts": 2, "successes": 0, "success_frequency": 0.0},
                    ],
                },
            }
        ],
    }
    assert prompt_robustness["evidence"] == {
        "attempt_ids": [
            "add-item/original/attempt-1",
            "add-item/original/attempt-2",
            "add-item/reworded/please/attempt-1",
            "add-item/reworded/please/attempt-2",
            "add-item/reworded/terse/attempt-1",
            "add-item/reworded/terse/attempt-2",
        ]
    }
    assert [entry["attempt_id"] for entry in scorecard["attempts"]] == prompt_robustness["evidence"]["attempt_ids"]
    assert json.loads(to_json(scorecard)) == scorecard


def test_the_agent_is_given_the_exact_text_of_each_prompt_variant_and_no_text_the_customer_did_not_supply():
    fixture = Fixture()

    fixture.sweep(fixture.experiment(attempts=2, prompt_variants=[PLEASE, SHOUTED]))

    # Every prompt the agent target was given: the Task's own, or a variant exactly as it was declared.
    assert fixture.agent.prompts == {
        "add-item/original/attempt-1": "Add one item to the list.",
        "add-item/original/attempt-2": "Add one item to the list.",
        "add-item/reworded/please/attempt-1": "Please add an item to my list.",
        "add-item/reworded/please/attempt-2": "Please add an item to my list.",
        "add-item/reworded/shouted/attempt-1": "  pls ADD 1 thing\n",
        "add-item/reworded/shouted/attempt-2": "  pls ADD 1 thing\n",
    }


def test_each_attempt_links_to_the_prompt_variant_it_ran_under_and_the_scorecard_keeps_the_exact_texts():
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=1, prompt_variants=[PLEASE, SHOUTED]))

    please = {"variant_id": "please", "text": "Please add an item to my list.", "source": SUPPORT_TEAM}
    shouted = {"variant_id": "shouted", "text": "  pls ADD 1 thing\n", "source": "taken from a support ticket"}
    assert [(entry["attempt_id"], entry["prompt_variant"]) for entry in scorecard["attempts"]] == [
        ("add-item/original/attempt-1", None),
        ("add-item/reworded/please/attempt-1", please),
        ("add-item/reworded/shouted/attempt-1", shouted),
    ]
    (experiment,) = scorecard["experiments"]
    assert experiment["task_prompt"] == "Add one item to the list."
    assert experiment["prompt_variants"] == [please, shouted]
    assert experiment["variation"] == {
        "condition_id": "reworded",
        "kind": "prompt",
        "description": "The agent is given the instruction in another wording.",
        "parameters": {},
    }
    # One Attempt under the Task's own prompt and one under each of the two variants.
    assert experiment["execution"] == {
        "status": "completed",
        "reason": None,
        "attempts_planned": 3,
        "attempts_recorded": 3,
        "attempts_completed": 3,
        "attempts_not_completed": [],
        "attempts_not_made": 0,
        "retries": 0,
    }
    # What the scorecard says an Attempt ran under is what the agent target was given for it.
    texts = {None: experiment["task_prompt"], **{variant["variant_id"]: variant["text"] for variant in (please, shouted)}}
    for entry in scorecard["attempts"]:
        variant_id = entry["prompt_variant"] and entry["prompt_variant"]["variant_id"]
        assert fixture.agent.prompts[entry["attempt_id"]] == texts[variant_id]
    assert json.loads(to_json(scorecard)) == scorecard


def test_every_prompt_variant_is_judged_by_the_task_success_rule_of_its_task():
    # The agent answers "Done." to every wording. The one rule of the Task reads the list for every
    # Attempt: under "terse" the list stayed empty, so those Attempts failed.
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=1))

    assert fixture.judged == {
        "add-item/original/attempt-1": "Add one item to the list.",
        "add-item/reworded/please/attempt-1": "Please add an item to my list.",
        "add-item/reworded/terse/attempt-1": "One more item.",
    }
    assert [(entry["attempt_id"], entry["task_outcome"]) for entry in scorecard["attempts"]] == [
        ("add-item/original/attempt-1", {"success": True, "source": OUTCOME_SOURCE}),
        ("add-item/reworded/please/attempt-1", {"success": True, "source": OUTCOME_SOURCE}),
        ("add-item/reworded/terse/attempt-1", {"success": False, "source": OUTCOME_SOURCE}),
    ]


FAILS = Condition(
    condition_id="add-fails",
    kind="fault",
    description="The to-do service refuses to add an item.",
    parameters={"add_fails": True},
)
PRESENTED = Condition(
    condition_id="renamed",
    kind="environment",
    description="The to-do service calls the title of an item its name.",
    parameters={"rename_fields": {"title": "name"}},
)
OF_NO_KIND = Condition(condition_id="other", description="A variation that declares no kind.")


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        (
            {"prompt_variants": []},
            "experiment 'add-item' declares a prompt variation, so it declares the prompt variants that the "
            "customer supplies or approves. The sweep generates none",
        ),
        ({"variation": FAILS}, "declares prompt variants, and its variation is not of the kind 'prompt'"),
        ({"variation": PRESENTED}, "declares prompt variants, and its variation is not of the kind 'prompt'"),
        ({"variation": OF_NO_KIND}, "declares prompt variants, and its variation is not of the kind 'prompt'"),
        ({"variation": None}, "declares prompt variants, and its variation is not of the kind 'prompt'"),
        ({"prompt_variants": ["Please add an item to my list."]}, "as a list of PromptVariant"),
        ({"prompt_variants": "Please add an item to my list."}, "as a list of PromptVariant"),
        (
            {"prompt_variants": [PLEASE, PromptVariant(variant_id="please", text="Add it.", source=SUPPORT_TEAM)]},
            "declares more than one prompt variant as 'please'",
        ),
        (
            {"prompt_variants": [PromptVariant(variant_id="same", text=PROMPT, source=SUPPORT_TEAM)]},
            "the prompt variant 'same' of experiment 'add-item' has the same text as the Task's own prompt",
        ),
        (
            {"prompt_variants": [PLEASE, PromptVariant(variant_id="again", text=PLEASE.text, source=SUPPORT_TEAM)]},
            "the prompt variant 'again' of experiment 'add-item' has the same text as the prompt variant 'please'",
        ),
        (
            {"variation": Condition(condition_id="reworded", kind="prompt", description="Reworded, and the service fails.", parameters={"add_fails": True})},
            "changes the wording of the instruction and nothing else, so its parameters are those of the baseline",
        ),
    ],
)
def test_a_prompt_variation_and_its_prompt_variants_are_declared_together_and_never_with_another_variation(
    declared, problem
):
    with pytest.raises(ValueError, match=problem):
        Fixture().experiment(attempts=1, **declared)


def test_prompt_robustness_is_rejected_before_any_attempt_when_no_experiment_declares_a_prompt_variation():
    fixture = Fixture()
    needs_variants = "'prompt_robustness' needs an experiment whose variation declares the kind 'prompt'"

    with pytest.raises(ValueError, match=needs_variants):
        fixture.sweep(fixture.experiment(attempts=2, variation=FAILS, prompt_variants=[]))
    with pytest.raises(ValueError, match=needs_variants):
        fixture.sweep(fixture.experiment(attempts=2, variation=None, prompt_variants=[]))
    assert fixture.agent.prompts == {}


def test_a_prompt_variation_and_a_fault_are_separate_experiments_and_each_metric_uses_its_own():
    # "add" declares the prompt variation: baseline 2/2 = 1. please 2/2 and terse 0/2, together 2/4 = 0.5.
    # Prompt robustness is 0.5 / 1 = 0.5.
    # "restock" declares the fault: baseline 2/2 = 1. The service refuses the item twice: 0/2 = 0.
    # Fault robustness is 0 / 1 = 0.
    # With the Attempts of both experiments in one comparison, prompt robustness would be
    # ((0.5 + 0) / 2) / 1 = 0.25.
    fixture = Fixture(task_ids=("add", "restock"))

    scorecard = fixture.sweep(
        fixture.experiment("add", attempts=2),
        fixture.experiment("restock", attempts=2, variation=FAILS, prompt_variants=[]),
        metrics=["prompt_robustness", "fault_robustness"],
    )

    assert len(scorecard["attempts"]) == 10
    prompt_robustness = metric(scorecard)
    assert prompt_robustness["value"] == 0.5
    assert prompt_robustness["counts"]["tasks"] == {"total": 1, "valid": 1, "missing": [], "excluded": []}
    assert all(attempt_id.startswith("add-item/") for attempt_id in prompt_robustness["attempts_used"]["attempt_ids"])
    fault_robustness = metric(scorecard, "fault_robustness")
    assert fault_robustness["value"] == 0
    assert fault_robustness["counts"]["tasks"] == {"total": 1, "valid": 1, "missing": [], "excluded": []}
    assert fault_robustness["attempts_used"]["attempt_ids"] == [
        "restock-item/original/attempt-1",
        "restock-item/original/attempt-2",
        "restock-item/add-fails/attempt-1",
        "restock-item/add-fails/attempt-2",
    ]
    # Only the Attempts under the prompt variation ran under a prompt variant, and the fault ran under the Task's own prompt.
    assert [
        (entry["intended_condition"].get("kind"), entry["prompt_variant"] and entry["prompt_variant"]["variant_id"])
        for entry in scorecard["attempts"]
    ] == [
        *((None, None), (None, None)),
        *(("prompt", "please"), ("prompt", "please"), ("prompt", "terse"), ("prompt", "terse")),
        *((None, None), (None, None), ("fault", None), ("fault", None)),
    ]
    assert [fixture.agent.prompts[f"restock-item/add-fails/attempt-{number}"] for number in (1, 2)] == [PROMPT] * 2


def test_outcome_consistency_rests_on_the_attempts_under_the_tasks_own_prompt_only():
    # Baseline 2/2: p = 1 and outcome consistency is 1. With the four Attempts under the prompt
    # variants mixed in, p would be 4/6 and the value (2 * 4/6 - 1)^2 = 1/9.
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=2), metrics=["outcome_consistency", "prompt_robustness"])

    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["evidence"] == {
        "attempt_ids": ["add-item/original/attempt-1", "add-item/original/attempt-2"]
    }
    assert metric(scorecard)["value"] == 0.5


def test_under_continuing_state_each_prompt_variant_continues_from_its_own_prepared_state():
    # Continuing state, 2 Attempts under each: the first leaves one item, which is success, and the
    # second continues from it and leaves two, which is failure.
    # Baseline 1/2 = 0.5. Each variant gets its own prepared list, so each repeats that: 1/2, together 2/4 = 0.5.
    # 0.5 / 0.5 = 1.
    # If "shouted" continued from the list that "please" left, it would start with two items and fail
    # twice: 1/4 = 0.25, and the value would be 0.5.
    fixture = Fixture()

    scorecard = fixture.sweep(
        fixture.experiment(attempts=2, starting_state="continuing", prompt_variants=[PLEASE, SHOUTED])
    )

    assert metric(scorecard)["value"] == 1
    assert {
        entry["attempt_id"]: [step["point"] for step in entry["lifecycle"]] for entry in scorecard["attempts"]
    } == {
        "add-item/original/attempt-1": ["prepare_state", "apply_condition", "verify_condition"],
        "add-item/original/attempt-2": ["apply_condition", "verify_condition", "clean_up"],
        "add-item/reworded/please/attempt-1": ["prepare_state", "apply_condition", "verify_condition"],
        "add-item/reworded/please/attempt-2": ["apply_condition", "verify_condition", "clean_up"],
        "add-item/reworded/shouted/attempt-1": ["prepare_state", "apply_condition", "verify_condition"],
        "add-item/reworded/shouted/attempt-2": ["apply_condition", "verify_condition", "clean_up"],
    }


class ReportsThePromptVariant(FakeControls):
    """Controls whose verify control reports which prompt variant it was told the Attempt runs under."""

    def verify_condition(self, context):
        observed = super().verify_condition(context)
        variant = context.prompt_variant
        return type(observed)(
            met=observed.met,
            source=observed.source,
            observations={**observed.observations, "prompt_variant": variant and variant.variant_id},
        )


def test_the_customer_controls_are_told_which_prompt_variant_an_attempt_runs_under():
    fixture = Fixture(controls=ReportsThePromptVariant)

    scorecard = fixture.sweep(fixture.experiment(attempts=1))

    assert [
        (entry["attempt_id"], entry["observed_condition"]["observations"]["prompt_variant"])
        for entry in scorecard["attempts"]
    ] == [
        ("add-item/original/attempt-1", None),
        ("add-item/reworded/please/attempt-1", "please"),
        ("add-item/reworded/terse/attempt-1", "terse"),
    ]


def test_an_attempt_under_a_prompt_variant_counts_only_when_the_verify_control_shows_the_intended_condition():
    # Baseline 2/2 = 1. Under "please", the service refused the first item although no fault was
    # intended: the verify control reports that the condition was not the intended one, and the Attempt,
    # a failure, is left out. The second succeeded: 1/1 = 1. The value is 1 / 1 = 1.
    # Counted, the first Attempt would give 1/2 = 0.5.
    fixture = Fixture(controls=lambda service: FakeControls(service, misapplied_for={"add-item/reworded/please/attempt-1"}))

    scorecard = fixture.sweep(fixture.experiment(attempts=2, prompt_variants=[PLEASE]))

    left_out = attempt(scorecard, "add-item/reworded/please/attempt-1")
    assert left_out["execution_status"] == "condition_not_met"
    assert left_out["task_outcome"] == {"success": False, "source": OUTCOME_SOURCE}
    assert left_out["prompt_variant"]["variant_id"] == "please"
    prompt_robustness = metric(scorecard)
    assert prompt_robustness["value"] == 1
    assert prompt_robustness["counts"]["prompt_attempts"] == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [
            {
                "reason": "the observed condition differs from the intended condition",
                "count": 1,
                "ids": ["add-item/reworded/please/attempt-1"],
            }
        ],
    }

def test_the_human_readable_report_shows_the_exact_text_of_each_prompt_variant():
    fixture = Fixture()
    scorecard = fixture.sweep(fixture.experiment(attempts=1, prompt_variants=[PLEASE, SHOUTED]))

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert report.value("/experiments/0/task_prompt") == "Add one item to the list."
    assert report.value("/experiments/0/prompt_variants/1/text") == "  pls ADD 1 thing\n"
    assert report.value("/attempts/2/prompt_variant/text") == "  pls ADD 1 thing\n"

def test_an_experiment_that_stopped_before_its_last_prompt_variant_cut_the_attempts_of_the_metric_short():
    # One Attempt under the Task's own prompt and one under each of two variants are planned. The
    # clean-up after the Attempt under "please" fails, so the Attempt under "terse" is not made.
    # The baseline has 1/1. "please" did not complete, so no Attempt under a variant is valid and the
    # Evaluator has nothing to compare. The planned Attempts were cut short: that is an execution
    # failure, and not an unavailable measurement of the agent.
    failing = {("clean_up", "add-item/reworded/please/attempt-1"): "the list could not be deleted"}
    fixture = Fixture(controls=lambda service: FakeControls(service, failures=failing))

    scorecard = fixture.sweep(fixture.experiment(attempts=1))

    assert [entry["attempt_id"] for entry in scorecard["attempts"]] == [
        "add-item/original/attempt-1",
        "add-item/reworded/please/attempt-1",
    ]
    (experiment,) = scorecard["experiments"]
    assert experiment["execution"]["status"] == "stopped"
    assert experiment["execution"]["attempts_planned"] == 3
    assert experiment["execution"]["attempts_not_made"] == 1
    prompt_robustness = metric(scorecard)
    assert prompt_robustness["cut_short"] == {"experiment_ids": ["add-item"], "task_ids": []}
    assert prompt_robustness["state"] is None
    assert prompt_robustness["value"] is None
    assert prompt_robustness["execution_failure"]["experiment_ids"] == ["add-item"]
    assert prompt_robustness["execution_failure"]["reason"].endswith("no valid Attempt under the prompt variation")

REVIEWED = OutcomeCorrection(
    attempt_id="add-item/reworded/terse/attempt-1",
    task_outcome=TaskOutcome(success=True, source="manual review of the service audit log"),
    provenance="J. Jansen on 2026-11-27: the check ran before the service had saved the item",
)


def test_a_task_outcome_corrected_under_a_prompt_variant_changes_prompt_robustness_and_keeps_the_variant():
    # Earlier: baseline 2/2 = 1. please 2/2 and terse 0/2, together 2/4 = 0.5, and 0.5 / 1 = 0.5.
    # A review finds that the first Attempt under "terse" succeeded: terse 1/2, together 3/4 = 0.75,
    # and 0.75 / 1 = 0.75.
    fixture = Fixture()
    earlier = json.loads(to_json(fixture.sweep(fixture.experiment(attempts=2))))
    assert metric(earlier)["value"] == 0.5

    recalculated = recalculate(earlier_scorecard=earlier, outcome_corrections=[REVIEWED])

    prompt_robustness = metric(recalculated)
    assert prompt_robustness["value"] == 0.75
    assert prompt_robustness["details"]["per_task"][0]["prompt"]["variants"] == [
        {"variant_id": "please", "attempts": 2, "successes": 2, "success_frequency": 1.0},
        {"variant_id": "terse", "attempts": 2, "successes": 1, "success_frequency": 0.5},
    ]
    # The correction changes the task outcome and nothing else: the Attempt keeps the text it ran under.
    corrected = attempt(recalculated, "add-item/reworded/terse/attempt-1")
    assert corrected["task_outcome"] == {"success": True, "source": "manual review of the service audit log"}
    assert corrected["prompt_variant"] == {"variant_id": "terse", "text": "One more item.", "source": SUPPORT_TEAM}
    assert recalculated["experiments"] == earlier["experiments"]
    assert metric(recalculated["superseded_results"][0])["value"] == 0.5


def test_supplied_evidence_whose_prompt_variant_is_not_what_was_recorded_is_refused():
    fixture = Fixture()
    earlier = json.loads(to_json(fixture.sweep(fixture.experiment(attempts=1))))
    attempt(earlier, "add-item/reworded/terse/attempt-1")["prompt_variant"] = {"variant_id": "terse"}

    with pytest.raises(RecalculationFailed, match="a prompt variant is recorded without its text, source"):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[REVIEWED])

def test_the_retry_of_an_attempt_under_a_prompt_variant_is_given_the_same_text_and_records_the_same_variant():
    # The invocation of the Attempt under "please" fails and is made again once. The retry succeeds:
    # baseline 1/1 = 1, please 1/1 = 1, and the value is 1. The Attempt that was retried is left out.
    fixture = Fixture({"add-item/reworded/please/attempt-1": "unreachable"})

    scorecard = fixture.sweep(
        fixture.experiment(attempts=1, prompt_variants=[PLEASE], limits=ExecutionLimits(max_retries=1))
    )

    assert attempt(scorecard, "add-item/reworded/please/attempt-1")["execution_status"] == "retried"
    retry = attempt(scorecard, "add-item/reworded/please/attempt-1/retry-1")
    assert retry["retry_of"] == "add-item/reworded/please/attempt-1"
    assert retry["prompt_variant"] == {
        "variant_id": "please",
        "text": "Please add an item to my list.",
        "source": SUPPORT_TEAM,
    }
    assert fixture.agent.prompts["add-item/reworded/please/attempt-1/retry-1"] == "Please add an item to my list."
    assert metric(scorecard)["value"] == 1
    assert metric(scorecard)["counts"]["prompt_attempts"]["valid"] == 1