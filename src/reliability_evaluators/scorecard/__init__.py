"""Scorecard: assembles metric results, acceptance assessments and attempt-level evidence.

The result is the machine-readable reliability scorecard. The human-readable report is
rendered from it, and both are delivered with the evidence to a location the caller chooses.
"""

from .acceptance import AcceptanceCriterion, CustomerAssessment, criteria_by_metric
from .assemble import SCHEMA_VERSION, PlannedAttempts, ScorecardStatus, ScoredMetric, build_scorecard, to_json
from .delivery import ArtifactLocation, ReportingFailure
from .report import render_report

__all__ = [
    "SCHEMA_VERSION",
    "AcceptanceCriterion",
    "ArtifactLocation",
    "CustomerAssessment",
    "PlannedAttempts",
    "ReportingFailure",
    "ScorecardStatus",
    "ScoredMetric",
    "build_scorecard",
    "criteria_by_metric",
    "render_report",
    "to_json",
]
