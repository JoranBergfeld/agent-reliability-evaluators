"""Scorecard: assembles metric results, acceptance assessments, token usage and attempt-level evidence.

The result is the machine-readable reliability scorecard. The human-readable report and the
text of a CI job summary are rendered from it, and they are delivered with the evidence to
a location the caller chooses. Token usage is metadata, reported apart from the metrics.
"""

from .acceptance import NOT_RECALCULATED, AcceptanceCriterion, CustomerAssessment, criteria_by_metric
from .assemble import (
    RESCORE,
    SCHEMA_VERSION,
    TOKEN_USAGE,
    CarriedMetric,
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
    "NOT_RECALCULATED",
    "RESCORE",
    "SCHEMA_VERSION",
    "TOKEN_USAGE",
    "AcceptanceCriterion",
    "ArtifactLocation",
    "CarriedMetric",
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
