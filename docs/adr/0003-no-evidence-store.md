# No evidence store; the execution platform governs access and retention

A reliability scorecard is traceable to attempt-level evidence, which can include sensitive customer task and agent data. The offering writes the scorecard, the evidence counts and the attempt-level evidence into the run or artifact context of the platform it executes on, which is Foundry or the customer's CI/CD platform, and keeps no store of its own. Access, retention and deletion are governed by that platform, so the controls the customer already has apply.

Decided in [Choose scorecard evidence access and retention](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/19).

## Rejected

- **An evidence store kept by the offering.**
- **An access, retention or deletion policy defined by the offering.** The platform's policy applies, so there is no retention guarantee across platforms.
- **Monitoring platform-side expiry or deletion, or revising a scorecard afterwards.** Evidence limitations are reported only where they can be detected at evaluation time, as unavailable measurements.

## Consequences

- The evidence behind a scorecard can expire or be deleted under the platform's lifecycle policy, and the scorecard does not change to say so. The scorecard and its counts are subject to the same policy.
- Anything that needs earlier evidence depends on the customer supplying it from their platform. The [spec](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/25) assumes this for recalculating after a corrected task outcome, and [the recalculation](../sweep/late-and-corrected-outcomes.md) works that way: the customer supplies the earlier scorecard, the earlier scorecard is not revised, and a new one marks its results as superseded.
