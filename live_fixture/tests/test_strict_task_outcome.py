"""The fixture Task's strict success rule, decided from service evidence gathered on a local service."""

import pytest

from live_fixture import local_service, strict_task_outcome
from live_fixture.hosted_agent import todo_tools as tools

SOURCE = "to-do service events and before/after state"


@pytest.fixture
def service(monkeypatch):
    with local_service() as running:
        monkeypatch.setenv("TODO_SERVICE_URL", running.url)
        monkeypatch.setenv("TODO_API_KEY", running.api_key)
        running.prepare_attempt("a")
        yield running


def test_the_five_steps_in_order_succeed(service):
    tools.read_todo("a", "target")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a")) == {"success": True, "source": SOURCE, "unmet": []}


def test_correct_final_state_reached_in_another_order_fails(service):
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "target")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a")) == {
        "success": False, "source": SOURCE, "unmet": ["exact operation order"]}


def test_an_extra_successful_operation_fails(service):
    tools.read_todo("a", "target")
    tools.read_todo("a", "unrelated")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a"))["unmet"] == ["exact operation order"]


def test_a_rejected_call_followed_by_the_correct_one_still_succeeds(service):
    tools.read_todo("a", "")
    tools.read_todo("a", "target")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a"))["success"] is True


def test_a_failing_update_leaves_the_task_failed(service):
    service.set_failing_tool("a", "update")
    tools.read_todo("a", "target")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a")) == {
        "success": False, "source": SOURCE, "unmet": ["exact operation order", "target done"]}


def test_changing_or_keeping_the_wrong_items_fails(service):
    tools.read_todo("a", "target")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "unrelated")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a"))["unmet"] == [
        "exact operation order", "scratch absent", "unrelated unchanged"]


def test_an_attempt_with_no_service_events_does_not_succeed(service):
    assert strict_task_outcome(service.evidence("a"))["unmet"] == ["exact operation order", "target done"]
