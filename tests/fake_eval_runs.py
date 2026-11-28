"""What the fake Foundry projects that run evaluations share: the routes of an eval and its runs, and their paging.

`tests/fake_foundry.py` runs evaluator code on submitted rows and `tests/fake_foundry_cloud_evaluation.py`
invokes an agent for every row. Each answers as its own kind of run was observed to answer, so the
bodies of an eval and of a run are theirs. How a request finds its handler, how a run is polled and
how its output items are paged is the same, and is here.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from reliability_evaluators.managed_scoring import FoundryRequestError


class FakeEvalRuns:
    """The eval and run routes of a fake project. A fake gives the handlers that make an eval and a run.

    It holds `runs` by identity, their `_output_items`, and how often each was polled in `_polls`.
    """

    # The body of the answer to a request no route takes.
    _NOT_FOUND = ""
    # What a run that has not ended reports besides its status, on top of what the run holds.
    _UNFINISHED: dict[str, Any] = {}
    # What a page without output items names as its first and its last item.
    _NO_ITEM: Any = None

    def _eval_run_routes(self) -> tuple[tuple[str, str, Any], ...]:
        return (
            ("POST", r"/openai/v1/evals", self._create_eval),
            ("POST", r"/openai/v1/evals/([^/]+)/runs", self._create_run),
            ("GET", r"/openai/v1/evals/([^/]+)/runs/([^/]+)", self._poll_run),
            ("POST", r"/openai/v1/evals/([^/]+)/runs/([^/]+)/cancel", self._cancel_run),
            ("GET", r"/openai/v1/evals/([^/]+)/runs/([^/]+)/output_items\?limit=(\d+)(?:&after=(\w+))?", self._items),
        )

    def _answer(self, routes, method: str, path: str, **sent: Any) -> Any:
        """What the handler of the route that takes the request answers. `sent` is given to it by name."""
        for route_method, pattern, handler in routes:
            matched = re.fullmatch(pattern, path)
            if method == route_method and matched:
                return copy.deepcopy(handler(*matched.groups(), **sent))
        raise FoundryRequestError(method, path, 404, self._NOT_FOUND)

    def _poll_run(self, eval_id, run_id, **_):
        self._polls[run_id] += 1
        run = self.runs[run_id]
        if run["status"] == "canceled":
            return run
        if self._never_finishes or self._polls[run_id] <= self._polls_until_finished:
            return {**run, "status": "in_progress", "error": None, **self._UNFINISHED}
        return run

    def _items(self, eval_id, run_id, limit, after, **_):
        items = self._output_items[run_id] if self.runs[run_id]["status"] == "completed" else []
        start = 0 if after is None else next(n for n, item in enumerate(items, start=1) if item["id"] == after)
        page = items[start : start + int(limit)]
        return {
            "data": page,
            "first_id": page[0]["id"] if page else self._NO_ITEM,
            "last_id": page[-1]["id"] if page else self._NO_ITEM,
            "has_more": start + len(page) < len(items),
        }
