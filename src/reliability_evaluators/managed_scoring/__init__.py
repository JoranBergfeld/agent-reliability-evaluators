"""Foundry-managed scoring: an Evaluator scored as a custom code evaluator in the customer's Foundry project.

- `artifact`: derives the Foundry evaluator from the canonical metric definition.
- `grader`: what that evaluator runs around the canonical definition, the encoding of the evidence it
  reads and the encoding of the result it returns. Pure.
- `scoring`: registers the derived evaluators, submits grouped evidence and recovers the results.
- `fidelity`: the check that decides whether a recovered result is authoritative or an unverified result.

The customer's Foundry project, reached as the customer's own identity, is `FoundryProject` of
`reliability_evaluators.foundry`. It is also offered here, where a customer first needs it.
"""

from ..foundry import AzureCliCredential, FoundryProject, FoundryRequestError
from .artifact import DerivationError, ManagedArtifact, derive_artifact, has_managed_evaluator
from .fidelity import FidelityCheck
from .scoring import (
    FoundryManagedScoring,
    ManagedFailure,
    ManagedResult,
    ManagedScoringFailure,
    RegisteredEvaluator,
    submitted_evidence_sha256,
)

__all__ = [
    "AzureCliCredential",
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
    "derive_artifact",
    "has_managed_evaluator",
    "submitted_evidence_sha256",
]
