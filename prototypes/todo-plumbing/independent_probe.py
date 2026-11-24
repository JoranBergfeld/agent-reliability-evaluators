"""Verify each registered metric separately in an already running disposable campaign."""

import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import run
from scenarios import EXPECTED, known_evidence


def main():
    group = os.environ.get("TODO_PROTOTYPE_GROUP", "")
    match = re.fullmatch(r"rg-todo-metrics-([0-9a-f]{10})", group)
    if not match:
        raise RuntimeError("TODO_PROTOTYPE_GROUP must identify the disposable metric campaign")
    account = run.az("account", "show")
    if account["name"] != "Sandbox" or account["id"] != run.SUBSCRIPTION:
        raise RuntimeError("Refusing to use a subscription other than the approved Sandbox")
    if not run.az("group", "exists", "--name", group):
        raise RuntimeError(f"Disposable resource group does not exist: {group}")
    suffix = match.group(1)
    account_name = f"todo-metrics-{suffix}"
    run.az("cognitiveservices", "account", "show", "--resource-group", group, "--name", account_name)
    run.ENDPOINT = f"https://{account_name}.services.ai.azure.com/api/projects/todo-plumbing"
    run.project_token(force_refresh=True)
    output = run.ROOT / f"independent-evaluators-{suffix}.json"
    observations = {"resource_group": group, "kind": "known synthetic evidence; one metric per evaluation",
                    "started_at": datetime.now(timezone.utc).isoformat(), "metrics": {}}

    def save():
        output.write_text(json.dumps(run.redact(observations), indent=2), encoding="utf-8")

    def probe(name):
        result = run.managed_scores(known_evidence(), [name], "independent-" + name.replace("_", "-"))
        score = result["scores"].get(name, {}).get("score")
        return {"expected": EXPECTED[name], "managed_score": score,
                "matches_expected": isinstance(score, (int, float))
                and math.isclose(score, EXPECTED[name], rel_tol=1e-12, abs_tol=1e-12),
                "result": result}

    save()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {pool.submit(probe, name): name for name in run.METRICS}
        for future in as_completed(pending):
            name = pending[future]
            try:
                observations["metrics"][name] = future.result()
            except Exception as error:
                observations["metrics"][name] = {"error": str(error), "matches_expected": False}
            save()
            print(f"{name}: {observations['metrics'][name].get('matches_expected', False)}", flush=True)
    observations["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
    if not all(item.get("matches_expected") for item in observations["metrics"].values()):
        raise RuntimeError("At least one independent evaluator did not match its known expected result")


if __name__ == "__main__":
    main()
