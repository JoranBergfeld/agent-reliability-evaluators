"""Foundry-managed scoring: an Evaluator scored as a custom code evaluator in the customer's Foundry project.

The Foundry evaluator is derived from the canonical metric definition (`artifact.py`), with
the code that reads the transported evidence inside it (`grader.py`).
"""

from .artifact import DerivationError, ManagedArtifact, derive_artifact, has_managed_evaluator

__all__ = [
    "DerivationError",
    "ManagedArtifact",
    "derive_artifact",
    "has_managed_evaluator",
]
