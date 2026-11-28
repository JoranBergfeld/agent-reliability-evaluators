"""The settings of a hosted agent go nowhere: Foundry returns them in plain text with the details of the agent.

`GET /agents/{name}` answers with the environment variables of a hosted agent, and those can hold a key.
The two targets that look an agent up are driven here against fake projects that answer the same way,
with a recognisable secret among the settings. Nothing the sweep delivers, nothing a target keeps and
nothing a refusal shows may hold it.
"""

import pytest

from live_fixture import local_service
from live_fixture.hosted_agent import todo_tools
from reliability_evaluators.agent_target.foundry_cloud_evaluation import (
    FoundryCloudEvaluationTarget,
    UnsupportedCombination,
)
from reliability_evaluators.agent_target.foundry_hosted import FoundryHostedAgentTarget, UnsupportedAgent
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import run_sweep
from tests.fake_foundry_cloud_evaluation import FakeAgent, FakeCloudEvaluationProject
from tests.fake_foundry_hosted import FakeHostedAgent, FakeTodoHostedAgent, fake_foundry_project
from tests.shown import shown_by
from tests.todo_experiment import ALL_TOOLS_WORK, TodoSetup
from tests.todo_fixture_sweep import TodoFixtureSweep

# What both fake projects return among the settings of a hosted agent.
SECRET = "synthetic-secret"


@pytest.fixture
def todo_service(monkeypatch):
    with local_service() as service:
        monkeypatch.setenv("TODO_SERVICE_URL", service.url)
        monkeypatch.setenv("TODO_API_KEY", service.api_key)
        yield service


def delivered_to(directory):
    return "".join(file.read_text(encoding="utf-8") for file in sorted(directory.iterdir()))


def hosted_target(project, setup=None):
    return FoundryHostedAgentTarget(
        project_endpoint=project.endpoint,
        agent_name="todo-hosted",
        credential=lambda: project.token,
        compose_input=None if setup is None else setup.instruction,
    )


def cloud_target(todo_service, **served):
    project = FakeCloudEvaluationProject(todo_service, {"todo-hosted": FakeAgent(kind="hosted", **served)})
    return FoundryCloudEvaluationTarget(project=project, agent_name="todo-hosted", poll_interval_seconds=0)


def test_a_sweep_of_a_hosted_agent_delivers_nothing_of_the_agents_settings(todo_service, tmp_path):
    setup = TodoSetup(todo_service, run_id="offline")
    with fake_foundry_project({"todo-hosted": FakeHostedAgent(FakeTodoHostedAgent(todo_tools))}) as project:
        target = hosted_target(project, setup)
        scorecard = run_sweep(
            experiments=[setup.experiment("todo", 2, ALL_TOOLS_WORK, target.agent_configuration())],
            agent_target=target,
            metrics=["outcome_consistency"],
            artifact_dir=tmp_path,
        )

    assert [attempt["execution_status"] for attempt in scorecard["attempts"]] == ["completed"] * 2
    assert SECRET not in to_json(scorecard) and SECRET not in delivered_to(tmp_path)
    # The target keeps what it needs of the agent's details, and that is not its settings.
    assert SECRET not in repr(vars(target))


def test_a_hosted_agent_that_is_refused_shows_nothing_of_its_settings():
    served = {"todo-hosted": FakeHostedAgent(FakeTodoHostedAgent(todo_tools), protocols=("invocations",))}
    with fake_foundry_project(served) as project:
        target = hosted_target(project)
        with pytest.raises(UnsupportedAgent) as refused:
            target.agent_configuration()

    assert SECRET not in shown_by(refused.value) and SECRET not in repr(vars(target))


def test_a_sweep_through_cloud_evaluation_delivers_nothing_of_a_hosted_agents_settings(todo_service, tmp_path):
    target = cloud_target(todo_service)
    experiment = TodoFixtureSweep(todo_service, state="run-1").experiment(
        attempts=2, agent_configuration=target.agent_configuration()
    )

    scorecard = run_sweep(
        experiments=[experiment], agent_target=target, metrics=["outcome_consistency"], artifact_dir=tmp_path
    )

    assert [attempt["execution_status"] for attempt in scorecard["attempts"]] == ["completed"] * 2
    assert SECRET not in to_json(scorecard) and SECRET not in delivered_to(tmp_path)
    assert SECRET not in repr(vars(target))


def test_a_hosted_agent_that_cloud_evaluation_refuses_shows_nothing_of_its_settings(todo_service):
    target = cloud_target(todo_service, protocols=("invocations",))

    with pytest.raises(UnsupportedCombination) as refused:
        target.agent_configuration()

    assert SECRET not in shown_by(refused.value) and SECRET not in repr(vars(target))
