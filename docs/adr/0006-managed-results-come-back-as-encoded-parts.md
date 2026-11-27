# A Foundry-managed result comes back as numbers, in parts

A Foundry code evaluator returns one number for each submitted row and nothing else. A metric result is more than a number: it has a state, and an unavailable measurement has a reason and evidence counts and no number at all. So a managed evaluator returns its result in parts, one submitted row a part: the plain metric value, the size of the encoded result, and the encoded result six bytes to a number. The encoded result is the result as the result contract writes it, deflated, behind a digest that also covers the evidence it was calculated from. The sweep puts the parts together, checks the digest, and reads the result with a strict reader. Decided for [#48](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/48), after live probes on 2026-11-27 that are recorded in [`docs/managed_scoring/readme.md`](../managed_scoring/readme.md).

The encoding carries the whole result and not a number and a reason, because the Evaluators differ in what they report: counts in several units, details of their own, and for some more than one value.

## Rejected

- **Raising for an unavailable measurement.** The run fails when every row raises, and returns nothing: no state, no reason, no counts. That was the gap.
- **Returning anything but a number.** A dict, a string, a tuple, a list, a boolean and `None` each failed the run when tried. Nothing an evaluator prints or declares comes back. The `skipped` output with a reason belongs to endpoint evaluators, which the offering does not use because they would need a hosted endpoint that receives customer data.
- **A sentinel number alone.** A number outside the scale can say that there is no value. It cannot say why, or name what was excluded.
- **One number for each count.** It would fix the shape of a result per metric in a second place, beside the canonical definition, and could not carry reasons or identities.
- **Several criteria over one row, one for each part**, which would submit the evidence once. Foundry refuses criteria of one run that map the same name from different fields, so each part would need an evaluator of its own.
- **Scoring in the customer's process and letting Foundry confirm a digest.** The calculation would then run in the customer's process, which is the other scoring path, and Foundry would hold no result.

## Consequences

- A managed sweep makes two runs for a metric where it made one, and the second has one row for every six bytes of the result. Foundry grades rows one after another at two to three seconds each when observed, so a small result costs about two minutes more than a single number did, and a sweep of some hundreds of Attempts a quarter of an hour.
- Every row carries the evidence, and the size limit is on a run as a whole. The evidence is therefore deflated in transport, and a result can still be too large to come back: for outcome consistency somewhere between 500 and 750 Attempts. That is a managed failure of the metric, never a part of a result.
- In the customer's Foundry project the runs show rows of large whole numbers with pass and fail labels. Only one row of the first run is the metric value, and for a metric without a value that row holds a marker one below the scale. The marker is checked and never reported.
- A row that Foundry does not grade is asked for again, because one missing part would otherwise cost the whole result.
- The value the Evaluator computed arrives exactly, inside the encoded result, beside the plain value Foundry returned. No fidelity rule compared the two when this was decided, and every managed result was unverified. [ADR 0008](0008-a-managed-result-is-authoritative-when-its-two-channels-agree.md) adds the rule.
- The canonical definitions and the Evidence model may now also use `zlib` and `base64` in the sandbox, and the derived evaluator depends on them.
