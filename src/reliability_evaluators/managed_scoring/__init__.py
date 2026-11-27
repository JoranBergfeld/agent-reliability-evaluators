"""Foundry-managed scoring: an Evaluator scored as a custom code evaluator in the customer's Foundry project.

- `artifact`: derives the Foundry evaluator from the canonical metric definition.
- `grader`: what that evaluator runs around the canonical definition, and the evidence encoding. Pure.
- `foundry`: the customer's Foundry project, reached as the customer's own identity.
- `scoring`: registers the derived evaluators, submits grouped evidence and recovers the results.
"""

from .artifact import DerivationError, ManagedArtifact, derive_artifact, has_managed_evaluator
from .foundry import FoundryProject, FoundryRequestError, azure_cli_token
from .scoring import FoundryManagedScoring, ManagedFailure, ManagedResult, ManagedScoringFailure, RegisteredEvaluator

__all__ = [
    "DerivationError",
    "FoundryManagedScoring",
    "FoundryProject",
    "FoundryRequestError",
    "ManagedArtifact",
    "ManagedFailure",
    "ManagedResult",
    "ManagedScoringFailure",
    "RegisteredEvaluator",
    "azure_cli_token",
    "derive_artifact",
    "has_managed_evaluator",
]
