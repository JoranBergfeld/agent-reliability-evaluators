"""The CI job summary: a short Markdown rendering of the machine-readable scorecard.

The offering ships no CI integration. This is text that it emits and that the customer's
pipeline publishes, for example by appending it to the job summary of its CI platform.

Like the report, it is rendered from the scorecard and from nothing else: every number in
it is copied from the scorecard, and nothing is calculated, combined or rounded for it.
Token usage has a section of its own, after the metrics and their acceptance assessments
and apart from them.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .acceptance import NOT_RECALCULATED
from .assemble import TOKEN_USAGE, to_json

_NO_VALUE = "\N{EM DASH}"

_METRICS = (
    "Each reliability metric is reported on its own. There is no combined score and no built-in threshold. "
    "The acceptance column is the assessment of the customer's own criterion, where one was declared."
)
_NO_USAGE_REPORTED = "This scorecard reports no token usage."
# How the sweep total is worded, by its coverage.
_SWEEP_TOTAL = {
    "complete": "Sweep total: **{total_tokens} tokens**. No use is missing a count.",
    "partial": (
        "Sweep total: **{total_tokens} tokens**, a partial sum. It adds the {uses_reported} counts that were "
        "reported. The {uses_missing} that are missing are listed below and are not counted as zero."
    ),
    "unavailable": (
        "Sweep total: **unavailable**. No count was reported, and {uses_missing} are missing. It is not zero."
    ),
}
_OTHER_COVERAGE = "Sweep total: {total_tokens} tokens, with coverage {coverage}."


def render_summary(scorecard: Mapping[str, Any]) -> str:
    """Render a scorecard as the text of a CI job summary, in Markdown.

    The summary names the status of the scorecard, lists each metric with its state, its
    value and its acceptance assessment, and then reports token usage under a heading of
    its own: for the sweep, per Evaluator, per shared preparation and per Attempt, with what
    is missing and why. Publishing it is the caller's: nothing here writes or sends it.
    """
    scorecard = json.loads(to_json(dict(scorecard)))
    lines = ["# Reliability scorecard", "", f"Scorecard status: `{_text(scorecard.get('status'))}`", ""]
    lines += ["## Metrics", "", _METRICS, ""]
    lines += _table(
        ("Metric", "State", "Value", "Scoring path", "Acceptance"),
        [_metric_row(entry) for entry in _records(scorecard.get("metrics"))],
    )
    lines += ["", "## Token usage", ""]
    usage = scorecard.get(TOKEN_USAGE)
    lines += _usage(usage) if isinstance(usage, dict) else [_NO_USAGE_REPORTED]
    return "\n".join(lines) + "\n"


def _metric_row(entry: Mapping[str, Any]) -> list[str]:
    state = entry.get("state")
    acceptance = entry.get("acceptance")
    # A Foundry-managed entry that a recalculation carried over rests on task outcomes that were corrected since.
    out_of_date = ", not recalculated" if entry.get(NOT_RECALCULATED) else ""
    return [
        _text(entry.get("name")),
        # A metric that records no state has no result: its Attempts were cut short, or its scoring failed.
        ("no result" if state is None else _text(state)) + out_of_date,
        _text(entry.get("value")),
        _text(entry.get("scoring_path")),
        _text(acceptance.get("status")) if isinstance(acceptance, dict) else _NO_VALUE,
    ]


def _usage(usage: Mapping[str, Any]) -> list[str]:
    lines = [_text(usage.get("meaning")), "", _text(usage.get("scope")), ""]
    sweep = usage.get("sweep") if isinstance(usage.get("sweep"), dict) else {}
    coverage = sweep.get("coverage")
    worded = _SWEEP_TOTAL.get(coverage, _OTHER_COVERAGE) if isinstance(coverage, str) else _OTHER_COVERAGE
    lines += [worded.format_map(_Shown(sweep)), ""]
    evaluators = _records(usage.get("evaluators"))
    if evaluators:
        lines += ["### Evaluators", "", _text(usage.get("evaluator_totals")), ""]
        lines += _table(
            ("Evaluator", "Tokens", "Coverage", "Overlapping", "Scoring"),
            [
                [
                    _code(entry.get("metric_id")),
                    _text(_of(entry, "total", "total_tokens")),
                    _text(_of(entry, "total", "coverage")),
                    _overlapping(entry),
                    # One count for scoring by code, and one for every managed run in Foundry.
                    ", ".join(map(_use, _records(entry.get("scoring")))) or _NO_VALUE,
                ]
                for entry in evaluators
            ],
        )
        lines.append("")
    preparations = _records(usage.get("shared_preparation"))
    if preparations:
        lines += ["### Shared preparation", ""]
        lines += _table(
            ("Preparation", "Tokens", "Used by", "Source"),
            [
                [
                    _code(entry.get("preparation_id")),
                    _use(entry),
                    ", ".join(map(_code, entry.get("used_by") or [])) or _NO_VALUE,
                    _text(entry.get("source")),
                ]
                for entry in preparations
            ],
        )
        lines.append("")
    attempts = _records(usage.get("attempts"))
    if attempts:
        lines += ["### Attempts", ""]
        lines += _table(
            ("Attempt", "Execution status", "Tokens", "Coverage", "Agent invocation", "Confidence follow-up"),
            [
                [
                    _code(entry.get("attempt_id")),
                    _text(entry.get("execution_status")),
                    _text(entry.get("total_tokens")),
                    _text(entry.get("coverage")),
                    _use(entry.get("invocation")),
                    _use(entry.get("confidence_follow_up")),
                ]
                for entry in attempts
            ],
        )
        lines.append("")
        sources = _sources(attempts)
        if sources:
            lines += ["### Where the counts of the Attempts come from", ""]
            lines += [f"- {_text(source)}" for source in sources]
            lines.append("")
    missing = _records(sweep.get("missing"))
    if missing:
        lines += ["### Missing usage", ""]
        lines += [_missing(entry) for entry in missing]
        lines.append("")
    return lines[:-1]


def _sources(attempts: list[dict[str, Any]]) -> list[str]:
    """Each source the uses of the Attempts state for their counts, once, in the order of the Attempts."""
    stated = [
        use.get("source")
        for entry in attempts
        for use in (entry.get("invocation"), entry.get("confidence_follow_up"))
        if isinstance(use, dict)
    ]
    return list(dict.fromkeys(source for source in stated if isinstance(source, str) and source))


def _overlapping(entry: Mapping[str, Any]) -> str:
    if not entry.get("overlapping"):
        return "no"
    return "yes, with " + ", ".join(map(_code, entry.get("overlaps_with") or []))


def _use(use: Any) -> str:
    """The count of one use: the number that was reported or is known, or that it is missing."""
    if not isinstance(use, dict):
        return _NO_VALUE
    return "missing" if use.get("total_tokens") is None else _text(use["total_tokens"])


def _missing(entry: Mapping[str, Any]) -> str:
    """One use whose count is missing, named as the scorecard names it, with the reason."""
    named = [
        f"{of} {_code(entry[key])}"
        for key, of in (
            ("attempt_id", "Attempt"),
            ("preparation_id", "preparation"),
            ("metric_id", "Evaluator"),
            ("run_id", "in managed run"),
        )
        if key in entry
    ]
    return f"- {_text(entry.get('use'))} of {' '.join(named)}: {_text(entry.get('reason'))}"


class _Shown(dict):
    """The fields of a total as they are shown in a sentence."""

    def __getitem__(self, key: str) -> str:
        return _text(self.get(key))


def _of(entry: Mapping[str, Any], group: str, key: str) -> Any:
    held = entry.get(group)
    return held.get(key) if isinstance(held, dict) else None


def _records(value: Any) -> list[dict[str, Any]]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def _table(columns: tuple[str, ...], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]


def _code(value: Any) -> str:
    return f"`{_text(value)}`"


def _text(value: Any) -> str:
    """A value as one cell of a Markdown table shows it. Numbers are their JSON text."""
    if value is None:
        return _NO_VALUE
    shown = value if isinstance(value, str) else json.dumps(value)
    return " ".join(shown.replace("|", "\\|").split())
