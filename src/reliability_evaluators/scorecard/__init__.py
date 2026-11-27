"""Scorecard: assembles metric results, acceptance assessments and attempt-level evidence.

The result is the machine-readable reliability scorecard. The human-readable report is
rendered from it, and both are delivered with the evidence to a location the caller chooses.
"""

from .acceptance import AcceptanceCriterion, CustomerAssessment, criteria_by_metric
from .assemble import (
    RESCORE,
    SCHEMA_VERSION,
    TOKEN_USAGE,
    CutShort,
    ExecutionRecord,
    MissingResult,
    ScorecardStatus,
    ScoredMetric,
    build_scorecard,
    read_execution_record,
    rescore_entry,
    to_json,
)
from .delivery import ArtifactLocation, ReportingFailure
from .report import render_report
from .summary import render_summary
from .usage import PreparationUsage, code_scoring_use, evaluator_token_usage, read_preparation_usage

__all__ = [
    "RESCORE",
    "SCHEMA_VERSION",
    "TOKEN_USAGE",
    "AcceptanceCriterion",
    "ArtifactLocation",
    "CustomerAssessment",
    "CutShort",
    "ExecutionRecord",
    "MissingResult",
    "PreparationUsage",
    "ReportingFailure",
    "ScorecardStatus",
    "ScoredMetric",
    "build_scorecard",
    "code_scoring_use",
    "criteria_by_metric",
    "evaluator_token_usage",
    "read_execution_record",
    "read_preparation_usage",
    "render_report",
    "render_summary",
    "rescore_entry",
    "to_json",
]
