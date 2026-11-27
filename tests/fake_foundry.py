"""A fake Foundry project for driving Foundry-managed scoring through the Evaluator sweep.

It answers the project REST requests that managed scoring makes, and it runs the registered
evaluator code on this machine the way the code-evaluator sandbox calls it. It behaves as a
live project was observed to behave (docs/managed_scoring/readme.md, "Observed live"):

- Registering an evaluator always adds a version, also when the same code is registered again.
  It needs the preview header.
- The versions of an evaluator are listed newest first, ten to a page, with a link to the next page.
- A criterion that names a version runs that version. Without one it runs the newest. An eval
  whose criterion names an evaluator or a version the project does not hold is refused.
- A run is queued, then in progress, then completed, failed or canceled.
- The sandbox refuses a source that calls `compile`, `eval`, `exec`, `globals` or `__import__`,
  as a function or as a method, before it runs anything.
- An evaluator that raises, or returns something other than a number, fails the whole run with
  no output items.
- Every result carries a threshold of 0.5 with a pass or fail label, and the declared range of
  the score is not enforced.
- A score that is a whole number comes back as an integer.

A test scripts what a live project could do to a run: alter the submitted item or the score,
report an error on a completed item, refuse every request, or never finish.
"""

from __future__ import annotations

import ast
import copy
import re
from typing import Any, Callable

from reliability_evaluators.managed_scoring import FoundryRequestError
from tests.managed_artifact import load_grader

REFUSED_CALLS = {"compile", "eval", "exec", "globals", "__import__"}
ENDPOINT = "https://fake.services.ai.azure.com/api/projects/fake"
VERSIONS_PAGE = 10
ALL_FAILED = "All examples failed due to invalid user input; please check the individual sample outputs for details."


class FakeFoundryProject:
    def __init__(
        self,
        *,
        alter_item: Callable[[dict], dict] | None = None,
        alter_score: Callable[[Any], Any] | None = None,
        sample_error: dict | None = None,
        refuses: FoundryRequestError | None = None,
        polls_until_finished: int = 0,
        never_finishes: bool = False,
    ):
        self._alter_item = alter_item
        self._alter_score = alter_score
        self._sample_error = sample_error
        self._refuses = refuses
        self._polls_until_finished = polls_until_finished
        self._never_finishes = never_finishes
        # Registered evaluator versions by name, oldest first.
        self.evaluators: dict[str, list[dict]] = {}
        self.evals: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self._output_items: dict[str, list[dict]] = {}
        self._polls: dict[str, int] = {}
        self.requests: list[tuple[str, str]] = []

    def register_other_code(self, name: str, code_text: str) -> str:
        """Register a version of an evaluator as someone else would have. Returns its version."""
        return self._register(name, {"name": name, "definition": {"type": "code", "code_text": code_text}})["version"]

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any:
        # A link the project returned is requested as it is.
        path = path.removeprefix(ENDPOINT)
        self.requests.append((method, path))
        if self._refuses is not None:
            raise self._refuses
        routes = (
            ("GET", r"/evaluators/([^/]+)/versions\?api-version=v1(?:&continuationToken=(\d+))?", self._list_versions),
            ("POST", r"/evaluators/([^/]+)/versions\?api-version=v1", self._register_with_preview),
            ("POST", r"/openai/v1/evals", self._create_eval),
            ("POST", r"/openai/v1/evals/([^/]+)/runs", self._create_run),
            ("GET", r"/openai/v1/evals/([^/]+)/runs/([^/]+)", self._poll_run),
            ("POST", r"/openai/v1/evals/([^/]+)/runs/([^/]+)/cancel", self._cancel_run),
            ("GET", r"/openai/v1/evals/([^/]+)/runs/([^/]+)/output_items\?limit=(\d+)(?:&after=(\w+))?", self._items),
        )
        for route_method, pattern, handler in routes:
            matched = re.fullmatch(pattern, path)
            if method == route_method and matched:
                return copy.deepcopy(handler(*matched.groups(), body=body, headers=headers or {}))
        raise FoundryRequestError(method, path, 404, "")

    def _list_versions(self, name, continuation, **_):
        newest_first = list(reversed(self.evaluators.get(name, [])))
        start = int(continuation or 0)
        following = start + VERSIONS_PAGE
        next_link = (
            f"{ENDPOINT}/evaluators/{name}/versions?api-version=v1&continuationToken={following}"
            if following < len(newest_first)
            else ""
        )
        return {"value": newest_first[start:following], "nextLink": next_link}

    def _register_with_preview(self, name, *, body, headers):
        if headers.get("Foundry-Features") != "Evaluations=V1Preview":
            raise FoundryRequestError(
                "POST", f"/evaluators/{name}/versions", 400, '{"error": {"innerError": {"code": "PreviewFeatureRequired"}}}'
            )
        return self._register(name, body)

    def _register(self, name, body):
        versions = self.evaluators.setdefault(name, [])
        version = str(len(versions) + 1)
        versions.append(
            {
                **copy.deepcopy(body),
                "id": f"azureai://accounts/fake/projects/fake/evaluators/{name}/versions/{version}",
                "version": version,
            }
        )
        return versions[-1]

    def _create_eval(self, *, body, **_):
        for criterion in body["testing_criteria"]:
            held = [version["version"] for version in self.evaluators.get(criterion["evaluator_name"], [])]
            if not held or criterion.get("evaluator_version", held[-1]) not in held:
                raise FoundryRequestError(
                    "POST", "/openai/v1/evals", 404, f"The evaluator {criterion['evaluator_name']} was not found."
                )
        eval_id = f"eval_{len(self.evals) + 1}"
        criteria = [
            {**criterion, "evaluator_version": criterion.get("evaluator_version", ""), "data_mapping": {}}
            for criterion in body["testing_criteria"]
        ]
        self.evals[eval_id] = {"id": eval_id, "name": body["name"], "testing_criteria": criteria}
        return self.evals[eval_id]

    def _create_run(self, eval_id, *, body, **_):
        run_id = f"evalrun_{len(self.runs) + 1}"
        rows = [row["item"] for row in body["data_source"]["source"]["content"]]
        error, items = self._execute(eval_id, run_id, rows)
        self.runs[run_id] = {
            "id": run_id,
            "eval_id": eval_id,
            "status": "failed" if error else "completed",
            "error": error,
            "report_url": f"https://ai.azure.com/nextgen/r/encoded-subscription-identifier/{eval_id}/{run_id}",
            "result_counts": {"total": len(items)},
        }
        self._output_items[run_id] = items
        self._polls[run_id] = 0
        return {**self.runs[run_id], "status": "queued", "error": None}

    def _poll_run(self, eval_id, run_id, **_):
        self._polls[run_id] += 1
        run = self.runs[run_id]
        if run["status"] == "canceled":
            return run
        if self._never_finishes or self._polls[run_id] <= self._polls_until_finished:
            return {**run, "status": "in_progress", "error": None}
        return run

    def _cancel_run(self, eval_id, run_id, **_):
        self.runs[run_id] = {**self.runs[run_id], "status": "canceled", "error": None}
        return self.runs[run_id]

    def _items(self, eval_id, run_id, limit, after, **_):
        items = self._output_items[run_id] if self.runs[run_id]["status"] == "completed" else []
        start = 0 if after is None else next(n for n, item in enumerate(items, start=1) if item["id"] == after)
        page = items[start : start + int(limit)]
        return {
            "data": page,
            "first_id": page[0]["id"] if page else None,
            "last_id": page[-1]["id"] if page else None,
            "has_more": start + len(page) < len(items),
        }

    def _execute(self, eval_id: str, run_id: str, rows: list[dict]) -> tuple[dict | None, list[dict]]:
        """Run every criterion on every row. One failure fails the run and leaves no items."""
        items = []
        for position, row in enumerate(rows):
            item = self._alter_item(copy.deepcopy(row)) if self._alter_item else row
            results = []
            for criterion in self.evals[eval_id]["testing_criteria"]:
                code_text = self._code_of(criterion)
                refused = _refused_call(code_text)
                if refused:
                    message = f"Invalid grader source: use of builtin '{refused}()' is not allowed in Python graders"
                    return {"code": "UserError", "message": message}, []
                try:
                    score = load_grader(code_text)({}, item)
                except Exception:
                    return {"code": "UserError", "message": ALL_FAILED}, []
                if isinstance(score, bool) or not isinstance(score, (int, float)):
                    return {"code": "UserError", "message": ALL_FAILED}, []
                results.append(self._result(criterion["name"], score))
            items.append(
                {
                    "object": "eval.run.output_item",
                    "id": str(position + 1),
                    "run_id": run_id,
                    "eval_id": eval_id,
                    "status": "completed",
                    "datasource_item_id": position,
                    "datasource_item": item,
                    "results": results,
                    "sample": {"error": self._sample_error, "usage": None, "model": "", "input": [], "output": []},
                }
            )
        return None, items

    def _code_of(self, criterion: dict) -> str:
        versions = self.evaluators[criterion["evaluator_name"]]
        pinned = criterion["evaluator_version"]
        (version,) = [v for v in versions if v["version"] == pinned] if pinned else versions[-1:]
        return version["definition"]["code_text"]

    def _result(self, name: str, score: float) -> dict:
        if self._alter_score:
            score = self._alter_score(score)
        if isinstance(score, float) and score.is_integer():
            score = int(score)
        if self._sample_error:
            return {"type": "azure_ai_evaluator", "name": name, "metric": name, "score": None, "status": "error"}
        return {
            "type": "azure_ai_evaluator",
            "name": name,
            "metric": name,
            "score": score,
            "label": "pass" if score >= 0.5 else "fail",
            "reason": None,
            "threshold": 0.5,
            "passed": score >= 0.5,
            "status": "completed",
        }


def _refused_call(code_text: str) -> str | None:
    for node in ast.walk(ast.parse(code_text)):
        if isinstance(node, ast.Call):
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called in REFUSED_CALLS:
                return called
    return None
