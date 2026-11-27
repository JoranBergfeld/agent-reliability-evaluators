"""Runs a derived Foundry evaluator on this machine, the way the code-evaluator sandbox calls it.

The artifact's source is executed on its own, as a module of its own, and its
`grade(sample, item)` is called with one item. Nothing of the offering's source is handed to it.
"""

from __future__ import annotations

import itertools
import sys
import types
from typing import Any

_loaded = itertools.count(1)


def load_grader(code_text: str):
    """Execute an artifact's source and return its `grade` function."""
    module = types.ModuleType(f"derived_foundry_evaluator_{next(_loaded)}")
    # Dataclasses look their module up while a class is created, so it is a module for that long.
    sys.modules[module.__name__] = module
    try:
        exec(compile(code_text, "<derived Foundry evaluator>", "exec"), module.__dict__)
    finally:
        del sys.modules[module.__name__]
    return module.grade


def grade(artifact, tasks) -> Any:
    """What the derived evaluator returns for grouped Attempt evidence submitted as one item."""
    return load_grader(artifact.code_text)({}, artifact.item(tasks))
