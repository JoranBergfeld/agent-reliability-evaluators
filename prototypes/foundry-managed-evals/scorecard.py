"""THROWAWAY: render captured API results without interpreting pass labels."""

import html
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
observations = json.loads(source.read_text(encoding="utf-8"))
cards = []


def pretty(value):
    return html.escape(json.dumps(value, indent=2, ensure_ascii=True))


for name, probe in observations["probes"].items():
    run = probe.get("run", {})
    status = run.get("status", "request rejected")
    rows = []
    evidence = []
    for item in probe.get("output_items", []):
        index = item["datasource_item_id"]
        submitted = probe["input"][index]
        returned = item["datasource_item"]
        if "item" in returned:
            returned = returned["item"]
        for result in item["results"]:
            completed = result["status"] == "completed"
            score = result["score"]
            value = str(score) if completed else "No completed measurement"
            if completed and not 0 <= score <= 1:
                value += " (outside documented range)"
            rows.append(
                "<tr><td>" + html.escape(submitted["case_id"]) + "</td><td>"
                + html.escape(result["name"]) + "</td><td>"
                + html.escape(value) + "</td></tr>"
            )
        evidence.append(
            "<details><summary>Submitted vs returned evidence: "
            + html.escape(submitted["case_id"]) + "</summary>"
            + "<h4>Submitted</h4><pre>" + pretty(submitted)
            + "</pre><h4>Foundry returned</h4><pre>" + pretty(returned)
            + "</pre><h4>Service diagnostics (not customer acceptance)</h4><pre>"
            + pretty(item["results"]) + "</pre></details>"
        )
    if not rows:
        rows.append('<tr><td colspan="3">No returned measurement. Do not substitute zero.</td></tr>')
    diagnostics = probe.get("api_error") or run.get("error") or probe.get("output_api_error")
    if diagnostics:
        evidence.append("<details><summary>Error details</summary><pre>" + pretty(diagnostics)
                        + "</pre></details>")
    cards.append(
        '<section><h2>' + html.escape(name) + '</h2><p>Run state: '
        + html.escape(status) + '</p><table><thead><tr><th>Evidence item</th>'
        + '<th>Evaluator</th><th>Observed value</th></tr></thead><tbody>'
        + "".join(rows) + '</tbody></table>' + "".join(evidence) + '</section>'
    )

page = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Foundry managed-code prototype scorecard</title>
<style>
body { font: 16px system-ui; max-width: 1100px; margin: 30px auto; padding: 0 20px; color: #24292f; }
section { border: 1px solid #d0d7de; padding: 18px; margin: 20px 0; border-radius: 6px; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; border-bottom: 1px solid #d0d7de; padding: 10px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f6f8fa; padding: 12px; }
details { margin-top: 15px; } summary { cursor: pointer; }
</style>
<h1>Foundry managed-code prototype: observed scores</h1>
<p><strong>Throwaway, synthetic evidence. All temporary Azure resource groups were deleted.</strong></p>
<p>This is an offline projection of captured API results, not a Foundry portal scorecard.
It does not combine reliability metrics or interpret platform pass/fail labels as customer
acceptance. Only the outcome-consistency probes implement a paper metric; other evaluators
are contract probes. Expand evidence to see numeric changes in returned structured data.
Opaque JSON strings preserved the tested confidence/outcome evidence exactly.</p>
"""
output = source.with_name("scorecard.html")
output.write_text(page + "".join(cards) + "</html>", encoding="utf-8")
print(output)
