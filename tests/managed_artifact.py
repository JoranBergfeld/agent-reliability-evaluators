"""Runs a derived Foundry evaluator on this machine, the way the code-evaluator sandbox calls it.

The artifact's source is executed on its own, in an empty namespace, and its `grade(sample, item)`
is called with one item. Nothing of the offering's source is handed to it.
"""

from __future__ import annotations

from typing import Any


def load_grader(code_text: str):
    """Execute an artifact's source and return its `grade` function."""
    namespace: dict[str, Any] = {"__name__": "derived_foundry_evaluator"}
    exec(compile(code_text, "<derived Foundry evaluator>", "exec"), namespace)
    return namespace["grade"]


def grade(artifact, tasks) -> Any:
    """What the derived evaluator returns for grouped Attempt evidence submitted as one item."""
    return load_grader(artifact.code_text)({}, artifact.item(tasks))
