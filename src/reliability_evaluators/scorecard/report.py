"""The human-readable report: one self-contained HTML rendering of the machine-readable scorecard.

The report is generic on purpose. It knows that a scorecard lists `metrics` and `attempts`,
that it says in `status` and `missing` whether it is complete, and that a recalculated one
lists the `superseded_results` it replaces. It describes the four metric states, the
execution failure that leaves a metric without one, and the scorecard statuses. Every other
field is laid out by its shape, so a field or a state that the scorecard gains is shown
without a change here.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from html import escape
from typing import Any

from .assemble import to_json

_STATES = {
    "available": "Available: a value was validly calculated from the evidence.",
    "unavailable": "Unavailable measurement: the metric could not be validly calculated from the evidence. "
    "No number is reported.",
    "not_applicable": "Not applicable: the protocol of the metric does not apply to the declared experiment. "
    "Nothing was measured.",
    "not_configured": "Not configured: the metric was not selected, so it was not measured.",
}
_UNDESCRIBED_STATE = "This report has no description of this state. It is shown as the scorecard records it."
# A metric that records no state and an execution failure instead has no result.
_EXECUTION_FAILURE = (
    "Execution failure: this metric has no result, because the Attempts that supply its evidence were cut "
    "short. Nothing was measured, and no Task failed because of it."
)

_STATUSES = {
    "complete": "Complete: every planned Attempt completed and every selected metric has a result.",
    "partial": "Partial: something that was planned is not in this scorecard. What is missing, and why, "
    "is listed under missing. The metrics that could still be calculated are shown.",
}
_UNDESCRIBED_STATUS = "This report has no description of this status. It is shown as the scorecard records it."

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,39}")


def render_report(scorecard: dict[str, Any]) -> str:
    """Render a scorecard as one self-contained HTML document.

    The report is rendered from the scorecard's machine-readable form and from nothing else.
    Every value it shows is marked with the JSON Pointer of that value in the scorecard
    (`data-scorecard`), in the text the scorecard's JSON has for it. Nothing is calculated,
    combined or rounded for display.
    """
    scorecard = json.loads(to_json(scorecard))
    if not isinstance(scorecard, dict):
        raise TypeError(f"a scorecard is a JSON object, got {type(scorecard).__name__}")
    return _Report(scorecard).page()


class _Report:
    def __init__(self, scorecard: dict[str, Any]):
        self._scorecard = scorecard
        # The position of the Attempt that an Attempt identity links to. An identity that
        # more than one Attempt carries is not linked to either.
        self._attempt_positions: dict[str, int] = {}
        attempts = scorecard.get("attempts")
        if _is_records(attempts):
            identities = [attempt.get("attempt_id") for attempt in attempts]
            carried = Counter(identity for identity in identities if isinstance(identity, str))
            self._attempt_positions = {
                identity: position
                for position, identity in enumerate(identities)
                if isinstance(identity, str) and carried[identity] == 1
            }

    def page(self) -> str:
        summary = {key: value for key, value in self._scorecard.items() if not _is_filled(value)}
        status = ""
        if "status" in summary:
            recorded = summary.pop("status")
            description = _STATUSES.get(recorded, _UNDESCRIBED_STATUS) if isinstance(recorded, str) else ""
            status = f'<p class="status">{self._value(recorded, _child("", "status"))} {escape(description)}</p>'
        sections = [self._section(key, value) for key, value in self._scorecard.items() if _is_filled(value)]
        return _PAGE.format(
            style=_STYLE,
            status=status,
            notice=_RECALCULATION_NOTICE if _is_records(self._scorecard.get("superseded_results")) else "",
            summary=self._fields(summary, "") if summary else "",
            sections="\n".join(sections),
        )

    def _section(self, key: str, value: Any) -> str:
        pointer = _child("", key)
        if key == "metrics" and _is_records(value):
            metrics = "\n".join(
                self._metric(entry, _child(pointer, position), f"metric-{position}")
                for position, entry in enumerate(value)
            )
            return f'<section id="metrics">\n<h2>Metrics</h2>\n{_METRICS_NOTE}\n{metrics}\n</section>'
        if key == "superseded_results" and _is_records(value):
            results = "\n".join(
                self._superseded_result(result, _child(pointer, position), position)
                for position, result in enumerate(value)
            )
            return (
                f'<section id="superseded-results">\n<h2>Superseded results</h2>\n{_SUPERSEDED_NOTE}\n'
                f"{results}\n</section>"
            )
        if key == "attempts" and _is_records(value):
            attempts = "\n".join(
                self._attempt(entry, _child(pointer, position), position) for position, entry in enumerate(value)
            )
            return f'<section id="attempts">\n<h2>Attempt-level evidence</h2>\n{_ATTEMPTS_NOTE}\n{attempts}\n</section>'
        if key == "missing" and _is_records(value):
            return (
                f'<section id="missing">\n<h2 data-key="missing">missing</h2>\n{_MISSING_NOTE}\n'
                f"{self._render(value, pointer)}\n</section>"
            )
        return f'<section>\n<h2 data-key="{escape(key)}">{escape(key)}</h2>\n{self._render(value, pointer)}\n</section>'

    def _superseded_result(self, result: dict[str, Any], pointer: str, position: int) -> str:
        """One earlier result: what superseded it, and its metrics, each marked as superseded."""
        metrics = result.get("metrics")
        shown_as_metrics = _is_records(metrics)
        rest = {key: value for key, value in result.items() if not (shown_as_metrics and key == "metrics")}
        parts = [self._fields(rest, pointer)] if rest else []
        if shown_as_metrics:
            parts += [
                self._metric(
                    entry,
                    _child(_child(pointer, "metrics"), at),
                    f"superseded-result-{position}-metric-{at}",
                    superseded=True,
                )
                for at, entry in enumerate(metrics)
            ]
        body = "\n".join(parts)
        return (
            f'<section class="superseded" id="superseded-result-{position}">\n'
            f"<h3>Superseded result</h3>\n{body}\n</section>"
        )

    def _metric(self, entry: dict[str, Any], pointer: str, element_id: str, *, superseded: bool = False) -> str:
        promoted = {key for key in ("name", "state") if key in entry and not isinstance(entry[key], (dict, list))}
        heading = self._value(entry["name"], _child(pointer, "name")) if "name" in promoted else "Unnamed metric"
        css, state = ("metric superseded" if superseded else "metric"), ""
        if superseded:
            state = _SUPERSEDED_METRIC
        if "state" in promoted:
            recorded = entry["state"]
            described = isinstance(recorded, str) and recorded in _STATES
            if recorded is None and _is_filled(entry.get("execution_failure"), dict):
                css += " execution-failure"
                description = _EXECUTION_FAILURE
            else:
                css += f" state-{recorded}" if described else " state-other"
                description = _STATES[recorded] if described else _UNDESCRIBED_STATE
            state += f'<p class="state">{self._value(recorded, _child(pointer, "state"))} {escape(description)}</p>'
        rest = {key: value for key, value in entry.items() if key not in promoted}
        fields = self._fields(rest, pointer, css="fields of-metric") if rest else ""
        return f'<article class="{css}" id="{element_id}">\n<h3>{heading}</h3>\n{state}\n{fields}\n</article>'

    def _attempt(self, attempt: dict[str, Any], pointer: str, position: int) -> str:
        identified = "attempt_id" in attempt and not isinstance(attempt["attempt_id"], (dict, list))
        heading = "Unidentified Attempt"
        if identified:
            heading = self._value(attempt["attempt_id"], _child(pointer, "attempt_id"))
        rest = {key: value for key, value in attempt.items() if not (identified and key == "attempt_id")}
        fields = self._fields(rest, pointer) if rest else ""
        return f'<section class="attempt" id="attempt-{position}">\n<h3>{heading}</h3>\n{fields}\n</section>'

    def _render(self, value: Any, pointer: str) -> str:
        """Lay a value out by its shape."""
        if _is_filled(value, dict):
            if all(_is_filled(row, dict) for row in value.values()):
                return self._table([(key, _child(pointer, key), row) for key, row in value.items()], keyed=True)
            return self._fields(value, pointer)
        if _is_filled(value, list):
            if _is_records(value):
                rows = [(None, _child(pointer, position), row) for position, row in enumerate(value)]
                return self._table(rows, keyed=False)
            return self._list(value, pointer)
        return self._value(value, pointer)

    def _fields(self, fields: dict[str, Any], pointer: str, css: str = "fields") -> str:
        rows = "".join(
            f"<tr>{_key(key, 'row')}<td>{self._render(value, _child(pointer, key))}</td></tr>\n"
            for key, value in fields.items()
        )
        return f'<table class="{css}">\n{rows}</table>'

    def _table(self, rows: list[tuple[str | None, str, dict[str, Any]]], *, keyed: bool) -> str:
        """One row per entry and one column per field that any entry has."""
        columns = list(dict.fromkeys(column for _, _, row in rows for column in row))
        head = ("<td></td>" if keyed else "") + "".join(_key(column, "col") for column in columns)
        body = ""
        for key, pointer, row in rows:
            cells = _key(key, "row") if key is not None else ""
            for column in columns:
                cell = self._render(row[column], _child(pointer, column)) if column in row else ""
                cells += f"<td>{cell}</td>"
            body += f"<tr>{cells}</tr>\n"
        return (
            f'<div class="scroll"><table class="rows">\n<thead><tr>{head}</tr></thead>\n'
            f"<tbody>\n{body}</tbody>\n</table></div>"
        )

    def _list(self, items: list[Any], pointer: str) -> str:
        short = all(not isinstance(item, (dict, list)) and len(_text(item)) <= 40 for item in items)
        entries = "".join(
            f"<li>{self._render(item, _child(pointer, position))}</li>" for position, item in enumerate(items)
        )
        return f'<ul class="{"values short" if short else "values"}">{entries}</ul>'

    def _value(self, value: Any, pointer: str) -> str:
        css = "v"
        if value is None or isinstance(value, (dict, list)):
            css += " absent"
        elif isinstance(value, str) and _IDENTIFIER.fullmatch(value):
            css += f" is-{value}"
        shown = f'<span class="{css}" data-scorecard="{escape(pointer)}">{escape(_text(value))}</span>'
        position = self._attempt_positions.get(value) if isinstance(value, str) else None
        if position is None or pointer == f"/attempts/{position}/attempt_id":
            return shown
        return f'<a href="#attempt-{position}">{shown}</a>'


def _is_filled(value: Any, kind: type | tuple[type, ...] = (dict, list)) -> bool:
    return isinstance(value, kind) and len(value) > 0


def _is_records(value: Any) -> bool:
    return _is_filled(value, list) and all(_is_filled(entry, dict) for entry in value)


def _key(key: str, scope: str) -> str:
    return f'<th scope="{scope}" data-key="{escape(key)}">{escape(key)}</th>'


def _text(value: Any) -> str:
    """The text a scalar is shown as. Strings are verbatim and numbers are their JSON text."""
    if value is None:
        return "\N{EM DASH}"
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return "none"
    return json.dumps(value)


def _child(pointer: str, key: str | int) -> str:
    return f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}"


_METRICS_NOTE = (
    '<p class="note">Every metric of the scorecard is listed, whatever its state. In the counts of a metric, '
    "valid is the denominator the calculation used, missing is evidence that should exist and does not, and "
    "excluded is evidence that exists and was deliberately not used.</p>"
)

_MISSING_NOTE = (
    '<p class="note">What was planned and is not in this scorecard, and why. Planned Attempts that were not made '
    "do not appear as Attempts. A planned Attempt that was made and did not complete is named here with how its "
    "execution ended, and is not a failed Task.</p>"
)

_RECALCULATION_NOTICE = (
    '<p class="notice">This scorecard is a recalculation: a task outcome was supplied or corrected after the run. '
    'It supersedes the earlier results, which are kept under <a href="#superseded-results">Superseded results</a>.</p>'
)

_SUPERSEDED_NOTE = (
    '<p class="note">The results that this scorecard replaces, oldest first, each with the task outcomes that were '
    "supplied or corrected to replace it. They are kept as a record. The current results are under Metrics.</p>"
)

_SUPERSEDED_METRIC = '<p class="superseded-mark">Superseded. This is not the current result of the metric.</p>\n'

_ATTEMPTS_NOTE = (
    '<p class="note">The scorecard\'s evidence for each Attempt. The execution status says how the running of an '
    "Attempt ended. It is not the task outcome: an Attempt that did not complete has no task outcome and is not "
    "a failed Task. An Attempt identity elsewhere in this report links to its evidence here.</p>"
)

_STYLE = """
:root{color-scheme:light dark;--ink:#1f2328;--muted:#59636e;--line:#d1d9e0;--page:#fff;--link:#0969da;
--ok:#1a7f37;--warn:#9a6700;--na:#8250df;--alert:#cf222e}
@media (prefers-color-scheme:dark){:root{--ink:#f0f6fc;--muted:#9198a1;--line:#3d444d;--page:#0d1117;
--link:#4493f8;--ok:#3fb950;--warn:#d29922;--na:#ab7df8;--alert:#f85149}}
body{font:16px/1.5 system-ui,sans-serif;color:var(--ink);background:var(--page);max-width:72rem;
margin:2rem auto;padding:0 1.25rem}
h1,h2,h3{line-height:1.25}
h2{margin-top:2.5rem;padding-bottom:.3rem;border-bottom:1px solid var(--line)}
a{color:var(--link)}
.note,footer{color:var(--muted)}
table{border-collapse:collapse}
th,td{text-align:left;vertical-align:top;padding:.3rem 1rem .3rem 0;border-bottom:1px solid var(--line)}
th{font-weight:600;white-space:nowrap}
thead th{border-bottom-width:2px}
td table{margin:-.3rem 0}
td tr:last-child>*{border-bottom:0}
.attempt{padding-bottom:.6rem;border-top:1px solid var(--line);scroll-margin-top:1rem}
.attempt h3{margin:.8rem 0 .2rem;font-size:1rem}
.attempt:target{outline:2px solid var(--link);outline-offset:.4rem}
.scroll{overflow-x:auto}
.v{white-space:pre-wrap;overflow-wrap:break-word}
.absent{color:var(--muted)}
ul.values{margin:0;padding-left:1.1rem}
ul.short{display:flex;flex-wrap:wrap;gap:.1rem 1rem;padding:0;list-style:none}
.metric{margin:1.25rem 0;padding:.1rem 1.25rem 1rem;border:1px solid var(--line);border-left:.5rem solid var(--alert);
border-radius:.4rem}
.state .v{display:inline-block;margin-right:.4rem;padding:0 .6rem;border:2px double var(--alert);border-radius:1rem;
color:var(--alert);font-weight:600}
.state-available{border-left-color:var(--ok)}
.state-available .state .v{border:2px solid var(--ok);color:var(--ok)}
.state-unavailable{border-left-color:var(--warn)}
.state-unavailable .state .v{border:2px solid var(--warn);color:var(--warn)}
.state-not_applicable{border-left-color:var(--na)}
.state-not_applicable .state .v{border:2px dashed var(--na);color:var(--na)}
.state-not_configured{border-left-color:var(--line)}
.state-not_configured h3{color:var(--muted)}
.state-not_configured .state .v{border:2px dotted var(--muted);color:var(--muted)}
.of-metric>tbody>tr>th[data-key=value]+td>.v{font-size:1.75rem;font-weight:600}
.execution-failure .state .v{border:2px solid var(--alert);background:var(--alert);color:var(--page)}
[data-scorecard$="/execution_status"],[data-scorecard$="/execution/status"]{padding:0 .5rem;
border:1px solid var(--alert);border-radius:1rem;color:var(--alert)}
.is-completed[data-scorecard$="/execution_status"],.is-completed[data-scorecard$="/execution/status"]{
border-color:var(--line);color:inherit}
.status .v{display:inline-block;margin-right:.4rem;padding:0 .6rem;border:2px double var(--alert);
border-radius:1rem;color:var(--alert);font-weight:600}
.status .is-complete{border:2px solid var(--ok);color:var(--ok)}
.status .is-partial{border:2px solid var(--warn);background:var(--warn);color:var(--page)}
.notice{padding:.6rem 1rem;border:1px solid var(--warn);border-radius:.4rem}
.metric.superseded{border-style:dashed}
.superseded-mark{color:var(--warn);font-weight:600}
.superseded .of-metric>tbody>tr>th[data-key=value]+td>.v{text-decoration:line-through}
"""

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reliability scorecard</title>
<style>{style}</style>
</head>
<body>
<header>
<h1>Reliability scorecard</h1>
<p>Each reliability metric is reported on its own. There is no combined score and no built-in threshold. \
A higher value is not always better: read the interpretation of each metric.</p>
{notice}
{status}
{summary}
</header>
<main>
{sections}
</main>
<footer>
<h2>How to read this report</h2>
<p>This report is a rendering of the machine-readable scorecard. Every value in it is copied from that \
scorecard. Nothing is calculated, combined or rounded for display.</p>
<p>Numbers are shown as the scorecard's JSON writes them: the shortest decimal text that reads back as the same \
double-precision floating-point number. A value that floating-point arithmetic cannot hold exactly is shown with \
the small error it was calculated with.</p>
<p>A dash (\N{EM DASH}) means that the scorecard records no value for a field. The word none means that a list or \
a group of fields is empty. Field names are the scorecard's own.</p>
<p>This report is one file, with no scripts and no external resources. The platform it was written to governs \
access to it, its retention and its deletion.</p>
</footer>
</body>
</html>
"""
