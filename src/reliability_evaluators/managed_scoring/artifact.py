"""Derivation of the Foundry evaluator artifacts from the canonical metric definitions.

A Foundry code evaluator is one Python source that defines `grade(sample, item) -> float`
and cannot import the offering. The artifact is therefore the source of the canonical
definition and of every module it imports from the offering, joined into one source in
import order. Nothing is rewritten: the statements are the ones the customer-process path
executes, less the imports between those modules, which one source does not need.

Nothing is stored. An artifact is derived when it is needed, from the source that is in use.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import inspect
import symtable
from dataclasses import dataclass, fields
from functools import lru_cache
from typing import Any, Iterable

from ..evaluators import EVALUATORS, RELIABILITY_METRICS, MetricDefinition
from ..evidence import Attempt, TaskEvidence
from . import grader

# The Attempt evidence each Evaluator is calculated from, by the field names of the Attempt
# record. Declaring an Evaluator here is what makes it derivable. An Attempt always travels
# with its identity, its Task and its execution status, and with nothing that is not named here.
MANAGED_ATTEMPT_EVIDENCE: dict[str, tuple[str, ...]] = {
    "outcome_consistency": ("task_outcome",),
}

# The sandbox limits a code evaluator has to fit. See docs/managed_scoring/readme.md for
# what was observed and what is only documented.
CODE_SIZE_LIMIT_BYTES = 256 * 1024
SANDBOX_PYTHON = (3, 12)
# Modules the sandbox accepted when observed. Anything else is refused here, before it can
# fail in a managed run.
SANDBOX_MODULES = frozenset(
    {
        "__future__",
        "bisect",
        "collections",
        "dataclasses",
        "enum",
        "fractions",
        "hashlib",
        "itertools",
        "json",
        "math",
        "re",
        "typing",
    }
)
# Calls the sandbox refused when observed, before running anything. It goes by the name
# that is called, as a function or as a method, so `re.compile` is refused as `compile` is.
SANDBOX_REFUSED_CALLS = frozenset({"compile", "eval", "exec", "globals", "__import__"})

_PACKAGE = __package__.rpartition(".")[0]
_GRADE = "def grade(sample, item):\n    return managed_value(DEFINITION, evaluate, item)\n"


class DerivationError(Exception):
    """A Foundry evaluator cannot be derived from the canonical definition as it is written."""


@dataclass(frozen=True, kw_only=True)
class ManagedArtifact:
    """The Foundry code evaluator derived from one canonical metric definition."""

    definition: MetricDefinition
    # The whole evaluator: the canonical definition, what it imports, and `grade`.
    code_text: str
    attempt_evidence: tuple[str, ...]

    @property
    def metric_id(self) -> str:
        return self.definition.metric.metric_id

    @property
    def evaluator_name(self) -> str:
        """The name the evaluator is registered under in a Foundry project."""
        return f"reliability-{self.metric_id.replace('_', '-')}"

    @property
    def evidence_field(self) -> str:
        return grader.evidence_field(self.metric_id)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.code_text.encode("utf-8")).hexdigest()

    def item(self, tasks: Iterable[TaskEvidence]) -> dict[str, str]:
        """The fields of a submitted item that carry this Evaluator's grouped Attempt evidence."""
        return {self.evidence_field: grader.encode_evidence(tasks, self.attempt_evidence)}

    def registration(self) -> dict[str, Any]:
        """The evaluator version to register: the code, the item it reads and the number it returns.

        Foundry requires a desirable direction. The canonical definition states none, because
        higher is not assumed to be better, so none is claimed here either.
        """
        metric, scale = self.definition.metric, self.definition.scale
        return {
            "name": self.evaluator_name,
            "evaluator_type": "custom",
            "categories": ["quality"],
            "display_name": f"{metric.name} (reliability Evaluator {self.definition.version})",
            "description": (
                f"{self.definition.measures} Derived from the canonical definition of {metric.metric_id}, "
                f"version {self.definition.version}. The threshold and the pass or fail label that Foundry "
                "adds to its result are not part of this metric."
            ),
            "definition": {
                "type": "code",
                "code_text": self.code_text,
                "init_parameters": {"type": "object", "properties": {}, "required": []},
                "data_schema": {
                    "type": "object",
                    "required": ["item"],
                    "properties": {
                        "item": {
                            "type": "object",
                            "properties": {self.evidence_field: {"type": "string"}},
                            "required": [self.evidence_field],
                        }
                    },
                },
                "metrics": {
                    "result": {
                        "type": "continuous",
                        "desirable_direction": "neutral",
                        "min_value": scale.minimum,
                        "max_value": scale.maximum,
                    }
                },
            },
        }


def has_managed_evaluator(metric_id: str) -> bool:
    return metric_id in MANAGED_ATTEMPT_EVIDENCE


@lru_cache(maxsize=None)
def derive_artifact(metric_id: str) -> ManagedArtifact:
    """Derive the Foundry code evaluator of one reliability metric from its canonical definition."""
    if metric_id not in MANAGED_ATTEMPT_EVIDENCE:
        if any(metric.metric_id == metric_id for metric in RELIABILITY_METRICS):
            raise DerivationError(f"{metric_id!r} has no Foundry-managed evaluator in this version")
        raise DerivationError(f"{metric_id!r} is not a reliability metric")
    attempt_evidence = MANAGED_ATTEMPT_EVIDENCE[metric_id]
    unknown = sorted(set(attempt_evidence) - {field.name for field in fields(Attempt)})
    if unknown:
        raise DerivationError(f"{unknown} is declared as Attempt evidence of {metric_id!r}, and an Attempt records no such thing")
    evaluator = EVALUATORS[metric_id]
    canonical = evaluator.evaluate.__module__
    modules = _Modules()
    # The grader first, so that everything it needs is there before the canonical definition.
    modules.add(grader.__name__)
    modules.add(canonical)
    for name in ("DEFINITION", "evaluate"):
        if name not in modules.defined_in(canonical):
            raise DerivationError(f"{canonical} does not define {name}, which `grade` calls")
    header = (
        f"# Foundry code evaluator for the reliability metric {metric_id}, derived from its canonical\n"
        f"# definition, version {evaluator.definition.version}. Do not edit it: derive and register a new version.\n\n"
    )
    code_text = header + modules.joined() + "\n\n\n" + _GRADE
    _require_sandbox_fit(metric_id, code_text)
    return ManagedArtifact(definition=evaluator.definition, code_text=code_text, attempt_evidence=attempt_evidence)


def _require_sandbox_fit(metric_id: str, code_text: str) -> None:
    version = ".".join(map(str, SANDBOX_PYTHON))
    try:
        tree = ast.parse(code_text, feature_version=SANDBOX_PYTHON)
    except SyntaxError as error:
        raise DerivationError(
            f"the evaluator derived for {metric_id!r} is not Python {version}, which the sandbox runs: {error}"
        ) from None
    for node in ast.walk(tree):
        called = isinstance(node, ast.Call) and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
        if called in SANDBOX_REFUSED_CALLS:
            raise DerivationError(
                f"the evaluator derived for {metric_id!r} calls {called}() on line {node.lineno} of its source, "
                "and the Foundry code-evaluator sandbox refuses a source that does"
            )
    size = len(code_text.encode("utf-8"))
    if size >= CODE_SIZE_LIMIT_BYTES:
        raise DerivationError(
            f"the evaluator derived for {metric_id!r} is {size} bytes, and a code evaluator "
            f"has to be under {CODE_SIZE_LIMIT_BYTES}"
        )


@dataclass(frozen=True)
class _Module:
    name: str
    # The statements that are kept, as written.
    body: str
    # Names the module itself binds at its top level, other than by importing them.
    defined: frozenset[str]
    # What each name imported from outside the offering is: (module, name in it).
    external: dict[str, tuple[str, str | None]]
    future: frozenset[str]


class _Modules:
    """Modules of the offering in the order one source has to hold them: imported before importing."""

    def __init__(self) -> None:
        self._modules: dict[str, _Module] = {}
        self._being_read: list[str] = []

    def add(self, name: str) -> None:
        if name in self._modules:
            return
        if name in self._being_read:
            cycle = " -> ".join([*self._being_read[self._being_read.index(name) :], name])
            raise DerivationError(f"modules that import each other cannot be joined into one source: {cycle}")
        self._being_read.append(name)
        self._modules[name] = self._read(name)
        self._being_read.pop()

    def defined_in(self, name: str) -> frozenset[str]:
        return self._modules[name].defined

    def joined(self) -> str:
        """One source holding every module, after checking that their names do not collide."""
        self._require_distinct_names()
        future = sorted({feature for module in self._modules.values() for feature in module.future})
        parts = [f"from __future__ import {', '.join(future)}"] if future else []
        for module in self._modules.values():
            if module.body:
                parts.append(f"# ---- {_relative(module.name)} ----\n\n{module.body}")
        return "\n\n\n".join(parts)

    def _read(self, name: str) -> _Module:
        module = importlib.import_module(name)
        try:
            source = inspect.getsource(module)
        except (OSError, TypeError):
            raise DerivationError(
                f"the source of {name} is not available. A Foundry evaluator is derived from the "
                "source of the canonical definitions, so they have to be copied as source"
            ) from None
        tree = ast.parse(source)
        package = name if hasattr(module, "__path__") else name.rpartition(".")[0]
        dropped: list[ast.stmt] = []
        external: dict[str, tuple[str, str | None]] = {}
        future: set[str] = set()
        own_imports: set[str] = set()
        for position, statement in enumerate(tree.body):
            if position == 0 and _is_docstring(statement):
                dropped.append(statement)
            elif isinstance(statement, ast.ImportFrom) and statement.level == 0 and statement.module == "__future__":
                future.update(alias.name for alias in statement.names)
                dropped.append(statement)
            elif isinstance(statement, ast.ImportFrom) and self._is_own(statement, package):
                own_imports.update(self._follow(name, package, statement))
                dropped.append(statement)
            elif _assigns_all(statement):
                dropped.append(statement)
        for statement in ast.walk(tree):
            if isinstance(statement, (ast.Import, ast.ImportFrom)) and statement not in dropped:
                external.update(self._external(name, package, statement, at_top_level=statement in tree.body))
        defined = frozenset(
            symbol.get_name()
            for symbol in symtable.symtable(source, name, "exec").get_symbols()
            if symbol.is_assigned() and not symbol.is_imported() and symbol.get_name() != "__all__"
        )
        return _Module(
            name=name,
            body=_without(source, tree, dropped, name),
            defined=defined,
            external=external,
            future=frozenset(future),
        )

    @staticmethod
    def _is_own(statement: ast.ImportFrom, package: str) -> bool:
        if statement.level:
            return True
        return statement.module == _PACKAGE or (statement.module or "").startswith(_PACKAGE + ".")

    def _follow(self, name: str, package: str, statement: ast.ImportFrom) -> list[str]:
        """Add the module of the offering that a statement imports from, and return the names it imports."""
        target = importlib.util.resolve_name("." * statement.level + (statement.module or ""), package)
        self.add(target)
        imported = self._modules[target]
        names = []
        for alias in statement.names:
            if alias.asname not in (None, alias.name) or alias.name == "*":
                raise DerivationError(
                    f"{name} imports {alias.name!r} from {target} under another name or as '*', "
                    "which one joined source cannot keep apart"
                )
            if not self._binds(alias.name):
                raise DerivationError(
                    f"{name} imports {alias.name!r} from {target}, which does not define it. "
                    "A module cannot be imported as an object into one joined source: import the names it defines"
                )
            names.append(alias.name)
        return names

    def _binds(self, name: str) -> bool:
        """Whether a module read so far defines or imports the name, so that the joined source has it."""
        return any(name in other.defined or name in other.external for other in self._modules.values())

    def _external(
        self, name: str, package: str, statement: ast.Import | ast.ImportFrom, *, at_top_level: bool
    ) -> dict[str, tuple[str, str | None]]:
        if isinstance(statement, ast.ImportFrom) and self._is_own(statement, package):
            raise DerivationError(
                f"{name} imports from the offering inside a function or a block (line {statement.lineno}). "
                "Only imports at the top of a module can be joined into one source"
            )
        if isinstance(statement, ast.Import):
            origins = {(alias.asname or alias.name.split(".")[0]): (alias.name, None) for alias in statement.names}
        else:
            origins = {(alias.asname or alias.name): (statement.module, alias.name) for alias in statement.names}
        for bound, (module, _) in origins.items():
            top = module.split(".")[0]
            if top == _PACKAGE:
                raise DerivationError(f"{name} imports {module} as a module, which one joined source cannot hold")
            if top not in SANDBOX_MODULES:
                raise DerivationError(
                    f"{name} imports {module}, which the Foundry code-evaluator sandbox is not known to provide"
                )
            if bound == "*":
                raise DerivationError(f"{name} imports '*' from {module}")
        # An import inside a function binds nothing at the top level.
        return origins if at_top_level else {}

    def _require_distinct_names(self) -> None:
        defined_by: dict[str, str] = {}
        imported_as: dict[str, tuple[str, tuple[str, str | None]]] = {}
        for module in self._modules.values():
            for name in module.defined:
                if name in defined_by:
                    raise DerivationError(
                        f"{defined_by[name]} and {module.name} both define {name!r}. "
                        "Joined into one source, one would replace the other"
                    )
                defined_by[name] = module.name
            for name, origin in module.external.items():
                if name in imported_as and imported_as[name][1] != origin:
                    raise DerivationError(
                        f"{imported_as[name][0]} and {module.name} import different things as {name!r}"
                    )
                imported_as[name] = (module.name, origin)
        for name in defined_by.keys() & imported_as.keys():
            raise DerivationError(
                f"{defined_by[name]} defines {name!r} and {imported_as[name][0]} imports another {name!r}"
            )


def _relative(name: str) -> str:
    """A module's name inside the offering, whatever the offering was copied in as."""
    return name[len(_PACKAGE) + 1 :]


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _assigns_all(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "__all__" for target in statement.targets
    )


def _without(source: str, tree: ast.Module, dropped: list[ast.stmt], name: str) -> str:
    """The source with whole statements left out and every other line as it is written."""
    lines = source.splitlines()
    leave_out: set[int] = set()
    for statement in dropped:
        leave_out.update(range(statement.lineno, statement.end_lineno + 1))
    for statement in tree.body:
        if statement not in dropped and not leave_out.isdisjoint(range(statement.lineno, statement.end_lineno + 1)):
            raise DerivationError(
                f"line {statement.lineno} of {name} holds an import and another statement, "
                "which cannot be told apart by line"
            )
    kept = [line for number, line in enumerate(lines, start=1) if number not in leave_out]
    return "\n".join(kept).strip("\n")
