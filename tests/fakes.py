"""Fakes for driving the Evaluator sweep through its public entry point."""

import threading
from dataclasses import dataclass

from reliability_evaluators.agent_target import AgentResponse, AttemptRequest


class FakeClock:
    """A clock that only moves when the fake agent target takes time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass(frozen=True)
class Scripted:
    """One scripted invocation: what happens, and how long it takes on the fake clock."""

    behaviour: str
    seconds: float = 0.0
    # The caller asks for the sweep to be cancelled while this invocation is in flight.
    cancels: bool = False


def taking(seconds: float, behaviour: str) -> Scripted:
    return Scripted(behaviour, seconds)


def cancelling(behaviour: str) -> Scripted:
    return Scripted(behaviour, cancels=True)


class FakeAgentTarget:
    """A scripted agent that works on a fake service.

    The script gives, per Task, what happens on each invocation in order: "success" and
    "failure" leave the service in the right or the wrong state, "error" makes the
    invocation itself fail, and "aborted" makes it end without an answer because the caller
    closed the connection. `cancelling(behaviour)` sets `cancel_requested` while the
    invocation is in flight. `taking(seconds, behaviour)` makes an invocation take time on
    the fake clock; the agent stops at the time limit it is given and raises TimeoutError,
    unless it was built not to honour time limits.
    The agent always answers "Done.", so its response says nothing about success; the
    task-success rule has to read `service_state`.
    """

    def __init__(
        self,
        script: dict[str, list[str | Scripted]],
        *,
        clock: FakeClock | None = None,
        honours_time_limits: bool = True,
    ):
        self._remaining = {
            task_id: [step if isinstance(step, Scripted) else Scripted(step) for step in behaviours]
            for task_id, behaviours in script.items()
        }
        self._clock = clock or FakeClock()
        self._honours_time_limits = honours_time_limits
        self.service_state: dict[str, str] = {}
        self.cancel_requested = False

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        step = self._remaining[request.task_id].pop(0)
        limit = request.timeout_seconds
        if self._honours_time_limits and limit is not None and step.seconds > limit:
            self._clock.advance(limit)
            raise TimeoutError(f"no answer within {limit:g} seconds")
        self._clock.advance(step.seconds)
        if step.cancels:
            self.cancel_requested = True
        if step.behaviour == "error":
            raise ConnectionError("the agent endpoint returned 503")
        if step.behaviour == "aborted":
            raise ConnectionAbortedError("the caller closed the connection")
        self.service_state[request.attempt_id] = "done" if step.behaviour == "success" else "open"
        return AgentResponse(output="Done.")


class MeetingAgentTarget:
    """An agent that answers only when two invocations are in flight at the same time.

    An invocation waits up to `wait_seconds` for another one to arrive. If none does, it
    fails, and so does every later invocation. Used to show through the scorecard whether
    Attempts overlapped.
    """

    def __init__(self, *, wait_seconds: float):
        self._meeting = threading.Barrier(2, timeout=wait_seconds)

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        try:
            self._meeting.wait()
        except threading.BrokenBarrierError:
            raise ConnectionError("no other invocation was in flight") from None
        return AgentResponse(output="Done.")
