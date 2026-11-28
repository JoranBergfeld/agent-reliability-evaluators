"""A bearer token is shown by nothing that fails: no error, nothing an error is chained to, no traceback, no scorecard.

Every place of the offering that sends a request to a Foundry project is failed here on purpose, with a
recognisable fake token: the Foundry hosted agent target, the Foundry prompt agent target and the
`FoundryProject` that Foundry-managed scoring and the Foundry cloud evaluation target send their requests
through. The targets are driven through the sweep where the sweep keeps the failure, and through the
interface the sweep and the customer call them through where the failure is raised to the caller.
No Azure access: the projects are fakes on this machine, or an address where nothing listens.
"""

import socket

import pytest

from live_fixture import local_service
from live_fixture.hosted_agent import todo_tools
from reliability_evaluators.agent_target import AttemptRequest, InvocationFailed
from reliability_evaluators.agent_target.foundry_hosted import FoundryHostedAgentTarget, UnsupportedAgent
from reliability_evaluators.agent_target.foundry_prompt_agent import FoundryPromptAgentTarget
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryProject, FoundryRequestError
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import Task, run_sweep
from tests.fake_foundry_hosted import FakeHostedAgent, FakeTodoHostedAgent, fake_foundry_project
from tests.fake_foundry_prompt_agent import fake_prompt_agent_project
from tests.shown import shown_by

# Not a token of anything. The fake projects do not accept it, so they answer HTTP 401.
TOKEN = "eyJ0eXAi.recognisable-fake-bearer-token.c2lnbmF0dXJl"
ATTEMPT = AttemptRequest(attempt_id="close/attempt-1", task_id="close", prompt="Close the item.")


def nothing_listens(scheme="http"):
    """The endpoint of a project at an address of this machine where nothing listens."""
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    return f"{scheme}://127.0.0.1:{port}/api/projects/nowhere"


def hosted_agent(endpoint, token=TOKEN):
    return FoundryHostedAgentTarget(project_endpoint=endpoint, agent_name="todo-hosted", credential=lambda: token)


def prompt_agent(endpoint):
    return FoundryPromptAgentTarget(project_endpoint=endpoint, agent_name="todo-prompt", credential=lambda: TOKEN)


def raised_by(call, expected):
    with pytest.raises(expected) as raised:
        call()
    return raised.value


def sweep_of_one_attempt(target):
    def never_decided(attempt, response):
        return TaskOutcome(success=True, source="synthetic")

    return run_sweep(
        tasks=[Task(task_id="close", prompt="Close the item.", success_rule=never_decided, attempts=1)],
        agent_target=target,
        metrics=["outcome_consistency"],
    )


@pytest.fixture
def hosted_project():
    with fake_foundry_project({"todo-hosted": FakeHostedAgent(FakeTodoHostedAgent(todo_tools))}) as running:
        yield running


def test_a_hosted_agent_look_up_that_the_project_refuses_does_not_show_the_token(hosted_project):
    error = raised_by(hosted_agent(hosted_project.endpoint).agent_configuration, InvocationFailed)

    assert "HTTP 401" in str(error)
    assert TOKEN not in shown_by(error)


def test_a_hosted_agent_that_is_not_supported_is_refused_without_showing_the_token():
    served = {"todo-hosted": FakeHostedAgent(FakeTodoHostedAgent(todo_tools), protocols=("invocations",))}
    with fake_foundry_project(served) as project:
        error = raised_by(hosted_agent(project.endpoint, project.token).agent_configuration, UnsupportedAgent)

    assert project.token not in shown_by(error)


def test_a_hosted_agent_in_a_project_that_cannot_be_reached_does_not_show_the_token():
    error = raised_by(hosted_agent(nothing_listens()).agent_configuration, InvocationFailed)

    assert "could not be reached" in str(error)
    assert TOKEN not in shown_by(error)


def test_a_failed_invocation_of_a_hosted_agent_is_kept_in_the_scorecard_without_the_token(hosted_project, capsys):
    scorecard = sweep_of_one_attempt(hosted_agent(hosted_project.endpoint))

    (attempt,) = scorecard["attempts"]
    assert attempt["execution_status"] == "failed" and "HTTP 401" in attempt["execution_error"]
    assert TOKEN not in to_json(scorecard)
    assert TOKEN not in "".join(capsys.readouterr())


def test_a_prompt_agent_invocation_that_the_project_refuses_does_not_show_the_token():
    with local_service() as todo_service, fake_prompt_agent_project(todo_service) as project:
        error = raised_by(lambda: prompt_agent(project.endpoint).invoke(ATTEMPT), InvocationFailed)

    assert "HTTP 401" in str(error)
    assert TOKEN not in shown_by(error)


def test_a_prompt_agent_in_a_project_that_cannot_be_reached_does_not_show_the_token(capsys):
    target = prompt_agent(nothing_listens())

    error = raised_by(lambda: target.invoke(ATTEMPT), InvocationFailed)
    scorecard = sweep_of_one_attempt(target)

    assert "could not be reached" in str(error)
    assert TOKEN not in shown_by(error)
    assert scorecard["attempts"][0]["execution_status"] == "failed"
    assert TOKEN not in to_json(scorecard)
    assert TOKEN not in "".join(capsys.readouterr())


def test_a_request_to_a_foundry_project_that_gets_no_answer_does_not_show_the_token():
    project = FoundryProject(nothing_listens("https"), token=lambda: TOKEN)

    error = raised_by(lambda: project.request("POST", "/openai/v1/evals", {"name": "synthetic"}), FoundryRequestError)

    assert error.status is None
    assert TOKEN not in shown_by(error)
    # The project holds the token it was given for the requests that follow, and does not show it.
    assert TOKEN not in repr(project) and TOKEN not in repr(vars(project))
