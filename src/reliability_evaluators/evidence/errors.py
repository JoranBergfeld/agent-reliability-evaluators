"""Rejection of evidence that cannot be accepted."""


class EvidenceError(ValueError):
    """Evidence that cannot be accepted as given. It is rejected, never repaired."""
