# A sweep too large for a managed run fails, and is not batched

Foundry-managed scoring has limits that scoring in the customer's own process does not have: the request that makes a run, with all its rows, was refused above 2,096,232 bytes, and a row whose evaluator ran for 60 seconds or more was never graded. A code evaluator returns its result in parts, one row a part, and every row carries the grouped evidence of the metric ([ADR 0006](0006-managed-results-come-back-as-encoded-parts.md)), so the request grows with the square of the sweep. A sweep of some hundreds of Attempts does not fit: 195 to 610 Attempts, depending on the metric, on synthetic sweeps.

When a limit is exceeded, the metric has no result. The scorecard is partial, the failure names the limit and how far the run was over it, the evidence is kept, and the customer can ask for a customer-process rescore. A run that would be too large is not made. Decided for [#49](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/49), after live probes on 2026-11-27 that are recorded in [`docs/managed_scoring/readme.md`](../managed_scoring/readme.md#limits). The [spec](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/25) named the limits and left the design open.

## Rejected

- **Scoring a part of the evidence in each run and combining the partial results in the customer's process.** The combination would be a second definition of the metric beside the canonical one ([ADR 0002](0002-one-canonical-metric-definition.md)), with rules of its own for every metric: task-balanced means, pooled pairs, ratios of two accuracies. It would also calculate in the customer's process what the customer chose to have calculated in Foundry.
- **Truncating or sampling the evidence so that it fits.** A value calculated from other evidence than the scorecard holds is not that metric.
- **Leaving the Attempt identities out of the evidence, or the details out of the result.** The result would no longer be the one the other scoring path gives, and a result could not name what it left out.
- **Scoring in the customer's process instead.** Customer-process scoring is never substituted for the path the customer chose.

## Not done

- **Asking for the parts of one encoded result in several runs.** Every row would still calculate the whole metric from all the evidence, so this is not a reduction, and it would lift the limit on the size of a run. The time a result takes would stay, at two to three seconds a row. It is a change to how a result comes back, and was left out of this decision.

## Consequences

- Foundry-managed scoring is for sweeps of up to a few hundred Attempts. The metrics whose results list every confidence/outcome pair or every measured Task reach the limit soonest.
- The limit on a run is enforced under what was observed, at 2,000,000 bytes, because what Foundry counts is not documented.
- A call that takes too long cannot be told from a row that Foundry did not grade for another reason, so the failure names the time limit without claiming it was the cause. Of the twelve Evaluators only sequential trajectory consistency comes near it, for Tasks with many Attempts of hundreds of actions.
