"""The coverage matrix as it is published: a rendering of the records of the runs, and of nothing else.

`python -m tests.full_matrix.published` writes the rendering between the two markers in
docs/coverage_matrix/readme.md. A default test compares what is there with the rendering, so
the published matrix cannot say more than the records do.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.full_matrix.reference import METRIC_IDS

PAGE = Path(__file__).resolve().parents[2] / "docs" / "coverage_matrix" / "readme.md"
EVIDENCE = PAGE.parent / "evidence"
BEGIN, END = "<!-- matrix: rendered from evidence/, do not edit -->", "<!-- matrix: end -->"
RUNS = {
    "prompt-customer": "Prompt agent, customer code",
    "hosted-customer": "Hosted agent, customer code",
    "prompt-cloud": "Prompt agent, cloud evaluation",
    "hosted-cloud": "Hosted agent, cloud evaluation",
}
PATHS = {"foundry_managed": "Foundry-managed", "customer_process": "Customer-process"}
NAMES = {
    "outcome_consistency": "Outcome consistency",
    "distributional_trajectory_consistency": "Distributional trajectory consistency",
    "sequential_trajectory_consistency": "Sequential trajectory consistency",
    "resource_consistency": "Resource consistency",
    "fault_robustness": "Fault robustness",
    "environment_robustness": "Environment robustness",
    "prompt_robustness": "Prompt robustness",
    "calibration": "Calibration",
    "strict_confidence_discrimination": "Strict confidence discrimination",
    "brier_based_score": "Brier-based score",
    "constraint_compliance": "Constraint compliance",
    "conditional_harm_severity": "Conditional harm severity",
}
STATUSES = ("worked", "unsupported", "unverified", "failed")
NOT_RUN = "unverified"


def records() -> dict[str, dict]:
    """The record of every run that was made, by the name of the run."""
    return {
        name: json.loads((EVIDENCE / f"{name}.json").read_text(encoding="utf-8"))
        for name in RUNS
        if (EVIDENCE / f"{name}.json").exists()
    }


def rendered(made: dict[str, dict]) -> str:
    return "\n\n".join([_overview(made), _counts(made), *(_run(name, made[name]) for name in RUNS if name in made)])


def _overview(made: dict[str, dict]) -> str:
    """Every cell: the status on the Foundry-managed path, then on the customer-process path."""
    lines = [
        "| Evaluator | " + " | ".join(RUNS.values()) + " |",
        "| --- |" + " --- |" * len(RUNS),
    ]
    for metric_id in METRIC_IDS:
        cells = []
        for name in RUNS:
            if name not in made:
                cells.append(f"{NOT_RUN}: not run")
                continue
            statuses = [made[name]["metrics"][metric_id]["cells"][path]["status"] for path in PATHS]
            cells.append(f"[{' · '.join(statuses)}](evidence/{name}.json)")
        lines.append(f"| {NAMES[metric_id]} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _counts(made: dict[str, dict]) -> str:
    lines = ["| Run | " + " | ".join(STATUSES) + " |", "| --- |" + " --- |" * len(STATUSES)]
    total = dict.fromkeys(STATUSES, 0)
    for name, title in RUNS.items():
        counted = dict.fromkeys(STATUSES, 0)
        for metric_id in METRIC_IDS:
            for path in PATHS:
                counted[made[name]["metrics"][metric_id]["cells"][path]["status"] if name in made else NOT_RUN] += 1
        for status in STATUSES:
            total[status] += counted[status]
        lines.append(f"| {title} | " + " | ".join(str(counted[status]) for status in STATUSES) + " |")
    lines.append("| **All 96 cells** | " + " | ".join(f"**{total[status]}**" for status in STATUSES) + " |")
    return "\n".join(lines)


def _run(name: str, record: dict) -> str:
    attempts = record["attempts"]
    witnessed = [attempt["witnessed"] for attempt in attempts]
    returned = [seen for seen in witnessed if seen["invocation_returned"]]
    where = record["environment"]
    facts = [
        f"Agent `{record['agent']}` through `{record['agent_target']}`.",
        f"Made from {record['started']} to {record['ended']} (UTC), with the source at commit `{where['offering_commit'][:7]}`.",
        f"{len(attempts)} Attempts: the invocation returned for {len(returned)} and raised for "
        f"{len(attempts) - len(returned)}.",
        f"Of those that returned, {sum(seen['success'] is True for seen in returned)} succeeded and "
        f"{sum(seen['success'] is False for seen in returned)} failed the strict rule, and "
        f"{sum(seen['violation_weight'] is not None for seen in returned)} changed `unrelated`.",
        f"{sum(seen['confidence_answer'] is not None for seen in witnessed)} confidence requests were answered.",
        f"The sweep took {record['sweep_seconds']} seconds, scoring in Foundry included, and its scorecard was "
        f"`{record['scorecard_status']}`.",
    ]
    lines = [
        f"### {RUNS[name]}",
        "",
        " ".join(facts) + f" Record: [`evidence/{name}.json`](evidence/{name}.json).",
        "",
        "| Evaluator | Expected | Foundry-managed | Customer-process | Why, where a cell did not work |",
        "| --- | --- | --- | --- | --- |",
    ]
    for metric_id in METRIC_IDS:
        entry = record["metrics"][metric_id]
        reasons = []
        for path in PATHS:
            cell = entry["cells"][path]
            if cell["status"] != "worked" and cell["why"] not in reasons:
                reasons.append(cell["why"])
        lines.append(
            f"| {NAMES[metric_id]} | {_expected(entry['expected'])} | "
            + " | ".join(_cell(entry, path) for path in PATHS)
            + f" | {' '.join(_escaped(reason) for reason in reasons)} |"
        )
    return "\n".join(lines)


def _expected(expected: dict | None) -> str:
    if expected is None:
        return "No value"
    exact = f" ({expected['exact']})" if expected["exact"] and "/1" != expected["exact"][-2:] else ""
    return f"`{expected['value']!r}`{exact}"


def _cell(entry: dict, path: str) -> str:
    result, status = entry["results"][path], entry["cells"][path]["status"]
    if result["state"] is None:
        returned = "no result"
    elif result["value"] is None:
        returned = "unavailable"
    else:
        returned = f"`{result['value']!r}`"
    return f"**{status}**: {returned}"


def _escaped(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def published() -> str:
    """What the page holds between the two markers."""
    page = PAGE.read_text(encoding="utf-8")
    return page[page.index(BEGIN) + len(BEGIN) : page.index(END)].strip()


if __name__ == "__main__":
    page = PAGE.read_text(encoding="utf-8")
    start, end = page.index(BEGIN) + len(BEGIN), page.index(END)
    PAGE.write_text(page[:start] + "\n\n" + rendered(records()) + "\n\n" + page[end:], encoding="utf-8")
