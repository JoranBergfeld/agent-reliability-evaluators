"""THROWAWAY: re-run only the managed Foundry scoring for saved proof evidence.

Usage: python rescore.py proof-<suffix>.json <combination>
Provisions a fresh disposable group (evaluators are registered there), scores the saved
grouped evidence in one managed run, compares with the customer and oracle values, then deletes the group.
"""

import json
import sys

import run as R
from metrics import METRICS

source_name, combination = sys.argv[1], sys.argv[2]
R.OUTPUT = R.ROOT / f"rescore-{R.SUFFIX}.json"
source = json.loads((R.ROOT / source_name).read_text(encoding="utf-8"))
saved = source["combinations"][combination]
R.OBS.update({"kind": "managed rescore of saved evidence", "source": source_name,
              "source_resource_group": source["resource_group"], "combination": combination,
              "reason": f"Original managed scoring failed: {saved['managed_combined'].get('error')}"})


def main():
    try:
        R.provision()
        evidence = saved["grouped_evidence"]
        scores = R.customer_scores(evidence)
        R.OBS["customer_scores_unchanged"] = all(
            scores[m]["value"] == saved["scores"][m]["value"] for m in METRICS)
        print("Scoring saved evidence in Foundry", flush=True)
        managed, retries = R.managed_with_retries(evidence, scores, list(METRICS), f"rescore-{combination}")
        eligible = [m for m in METRICS if scores[m]["value"] is not None]
        R.OBS["result"] = {
            "scores": scores, "managed_combined": managed, "managed_individual_retries": retries,
            "summary": {
                "available_metrics": len(eligible),
                "combined_managed_returned": len(managed.get("scores", {})),
                "all_available_in_one_managed_run": set(managed.get("scores", {})) >= set(eligible),
                "all_match_independent": all(scores[m].get("managed_matches_independent") for m in eligible),
                "target_metrics": {m: {k: scores[m].get(k) for k in
                                       ("value", "independent_expected", "managed_score",
                                        "managed_matches_independent")}
                                   for m in ("strict_confidence_discrimination", "conditional_harm_severity")},
            },
        }
        print(json.dumps(R.OBS["result"]["summary"], indent=2), flush=True)
        R.save()
    except Exception as error:
        R.OBS["fatal_error"] = {"type": type(error).__name__, "message": R.redact(str(error))}
        R.save()
        raise
    finally:
        R.cleanup()


if __name__ == "__main__":
    main()
