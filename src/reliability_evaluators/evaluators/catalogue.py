"""The twelve reliability metrics, in the order of paper v3 Table 2.

Every scorecard lists all twelve. A metric that was not selected is still named,
so that it is reported as not configured and never silently omitted.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReliabilityMetric:
    metric_id: str
    name: str
    dimension: str


OUTCOME_CONSISTENCY = ReliabilityMetric("outcome_consistency", "Outcome consistency", "consistency")
DISTRIBUTIONAL_TRAJECTORY_CONSISTENCY = ReliabilityMetric(
    "distributional_trajectory_consistency", "Distributional trajectory consistency", "consistency"
)
SEQUENTIAL_TRAJECTORY_CONSISTENCY = ReliabilityMetric(
    "sequential_trajectory_consistency", "Sequential trajectory consistency", "consistency"
)
RESOURCE_CONSISTENCY = ReliabilityMetric("resource_consistency", "Resource consistency", "consistency")
FAULT_ROBUSTNESS = ReliabilityMetric("fault_robustness", "Fault robustness", "robustness")
ENVIRONMENT_ROBUSTNESS = ReliabilityMetric("environment_robustness", "Environment robustness", "robustness")
PROMPT_ROBUSTNESS = ReliabilityMetric("prompt_robustness", "Prompt robustness", "robustness")
CALIBRATION = ReliabilityMetric("calibration", "Calibration", "predictability")
STRICT_CONFIDENCE_DISCRIMINATION = ReliabilityMetric(
    "strict_confidence_discrimination", "Strict confidence discrimination", "predictability"
)
BRIER_BASED_SCORE = ReliabilityMetric("brier_based_score", "Brier-based score", "predictability")
CONSTRAINT_COMPLIANCE = ReliabilityMetric("constraint_compliance", "Constraint compliance", "operational_safety")
CONDITIONAL_HARM_SEVERITY = ReliabilityMetric(
    "conditional_harm_severity", "Conditional harm severity", "operational_safety"
)

RELIABILITY_METRICS: tuple[ReliabilityMetric, ...] = (
    OUTCOME_CONSISTENCY,
    DISTRIBUTIONAL_TRAJECTORY_CONSISTENCY,
    SEQUENTIAL_TRAJECTORY_CONSISTENCY,
    RESOURCE_CONSISTENCY,
    FAULT_ROBUSTNESS,
    ENVIRONMENT_ROBUSTNESS,
    PROMPT_ROBUSTNESS,
    CALIBRATION,
    STRICT_CONFIDENCE_DISCRIMINATION,
    BRIER_BASED_SCORE,
    CONSTRAINT_COMPLIANCE,
    CONDITIONAL_HARM_SEVERITY,
)
