# A managed result is authoritative when its two channels agree

A result of Foundry-managed scoring comes back twice: as the plain number Foundry returns as the score, and inside the encoded result that the Evaluator returns in parts ([ADR 0006](0006-managed-results-come-back-as-encoded-parts.md)). The sweep compares the two when it recovers the result. A value that is the same number on both is authoritative. A value that differs is an unverified result: the plain number stays the reported value, the encoded one is shown beside it, and no acceptance criterion is assessed on it. A result without a value is authoritative when the plain channel returned the marker for that and the encoded result has no value. Decided for [#47](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/47), after live probes on 2026-11-27 that are recorded in [`docs/managed_scoring/readme.md`](../managed_scoring/readme.md).

Agreement shows that the reported value is the one the Evaluator returned in Foundry, unchanged on the way back. It does not show that the calculation is right: both channels carry the output of the same evaluator. That the derived evaluator calculates what the canonical definition does is shown by the Evaluator-seam vectors, on a development machine and not in Foundry.

The rule is applied to each result, so it does not rest on the plain channel always preserving a number. In the probes it preserved every double that was tried but negative zero, which lost its sign.

## Rejected

- **Comparing with a calculation in the customer's own process.** It would be the strongest check of the value. It would also make every managed result depend on a customer-process calculation, which is the other scoring path, and a disagreement would invite reporting the customer-process value in its place. The managed path reports what Foundry calculated ([#18](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/18)). A customer who wants the comparison asks for a customer-process rescore, which is reported as a result of its own.
- **Reporting the encoded value and ignoring the plain one.** The encoded result is behind a digest, so it is the better evidence of what the Evaluator computed. But the plain number is what the customer's Foundry project shows for the metric. A scorecard that called another number authoritative would disagree with the project without saying so, and no result could ever be an unverified result.
- **Trusting the plain channel because it preserved the probed values.** A sample of doubles is not every double, and the platform can change how it writes a number. A check for each result costs nothing more, since the encoded result comes back anyway.
- **Comparing to a tolerance.** Two adjacent doubles are different values. A tolerance would hide the alteration the check exists to find.
- **Reporting a result without a value as unverified, or with no fidelity.** It would mark every unavailable measurement from Foundry as doubtful, although both channels said the same. Its state is what keeps an acceptance criterion from being assessed.

## Consequences

- An acceptance criterion is assessed on a Foundry-managed value, where every such value used to be not assessed.
- `fidelity: authoritative` on a managed result says less than on a customer-process result: that the value came back unchanged, not that the customer's process calculated it. `managed_scoring.fidelity_check` says so in words in every scorecard.
- Only the value travels on both channels. The counts, the details and the reason of a result rest on the digest of the encoded result alone, and so do further values that an Evaluator reports in its details.
- The digest has no secret. The rule finds accidental alteration, not a deliberate change by someone who controls what the project returns.
- A value that differs on the two channels leaves a complete scorecard with an unverified result, and a plain value outside the scale of the metric leaves the metric without a result, as before.
