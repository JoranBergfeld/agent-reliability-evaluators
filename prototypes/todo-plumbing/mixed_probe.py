"""Probe all twelve managed graders together when some evidence is unavailable."""

import json
import os
import re
import time
from datetime import datetime, timezone

import run
from scenarios import unavailable_evidence


def main():
    group = os.environ.get("TODO_PROTOTYPE_GROUP", "")
    match = re.fullmatch(r"rg-todo-metrics-([0-9a-f]{10})", group)
    if not match:
        raise RuntimeError("TODO_PROTOTYPE_GROUP must identify the disposable metric campaign")
    account = run.az("account", "show")
    if account["name"] != "Sandbox" or account["id"] != run.SUBSCRIPTION:
        raise RuntimeError("Refusing to use a subscription other than the approved Sandbox")
    suffix = match.group(1)
    source = run.ROOT / f"observations-{suffix}.json"
    output = run.ROOT / f"mixed-availability-{suffix}.json"

    deadline = time.monotonic() + 2400
    while time.monotonic() < deadline:
        if source.exists():
            try:
                campaign = json.loads(source.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(5)
                continue
            if campaign.get("fatal_error"):
                raise RuntimeError(f"Campaign stopped before the probe: {campaign['fatal_error']}")
            registration = campaign.get("evaluator_registration", {})
            if len(registration) == len(run.METRICS) and not all(
                item.get("registered") for item in registration.values()
            ):
                raise RuntimeError("Not all twelve managed evaluators were registered")
            group_evidence = campaign.get("combinations", {}).get("prompt-customer", {}).get("grouped_evidence")
            if len(registration) == len(run.METRICS) and group_evidence:
                break
        time.sleep(5)
    else:
        raise RuntimeError("Timed out waiting for the first live group and twelve registered evaluators")

    if not run.az("group", "exists", "--name", group):
        raise RuntimeError(f"Disposable resource group no longer exists: {group}")
    run.ENDPOINT = f"https://todo-metrics-{suffix}.services.ai.azure.com/api/projects/todo-plumbing"
    run.project_token(force_refresh=True)
    observations = {
        "kind": "exploratory all-twelve combined runs with mixed applicability",
        "resource_group": group,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cases": {},
    }

    for label, evidence in (
        ("synthetic_missing_severity", unavailable_evidence("conditional_harm_severity")),
        ("live_prompt_customer", group_evidence),
    ):
        scores = run.customer_scores(evidence)
        result = run.managed_scores(evidence, list(run.METRICS), f"mixed-{label.replace('_', '-')}")
        if "evaluation" not in result:
            raise RuntimeError(f"Could not run mixed-availability probe {label}: {result.get('error')}")
        run.compare_managed(result["scores"], scores)
        observations["cases"][label] = {"customer_scores": scores, "managed": result}
        output.write_text(json.dumps(run.redact(observations), indent=2), encoding="utf-8")
        status = result["evaluation"]["run"]["status"]
        print(f"{label}: managed status {status}, {len(result['scores'])} scores returned", flush=True)

    observations["finished_at"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(run.redact(observations), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
