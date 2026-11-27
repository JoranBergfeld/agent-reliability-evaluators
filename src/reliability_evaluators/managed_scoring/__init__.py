"""Foundry-managed scoring: an Evaluator scored as a custom code evaluator in the customer's Foundry project.

- `artifact`: derives the Foundry evaluator from the canonical metric definition.
- `grader`: what that evaluator runs around the canonical definition, the encoding of the evidence it
  reads and the encoding of the result it returns. Pure.
- `foundry`: the customer's Foundry project, reached as the customer's own identity.
- `scoring`: registers the derived evaluators, submits grouped evidence and recovers the results.
- `fidelity`: the check that decides whether a recovered result is authoritative or an unverified result.
"""

from .artifact import DerivationError, ManagedArtifact, derive_artifact, has_managed_evaluator
from .fidelity import FidelityCheck
from .foundry import FoundryProject, FoundryRequestError, azure_cli_token
from .scoring import (
    FoundryManagedScoring,
    ManagedFailure,
    ManagedResult,
    ManagedScoringFailure,
    RegisteredEvaluator,
    submitted_evidence_sha256,
)

__all__ = [
    "DerivationError",
    "FidelityCheck",
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
    "submitted_evidence_sha256",
]
