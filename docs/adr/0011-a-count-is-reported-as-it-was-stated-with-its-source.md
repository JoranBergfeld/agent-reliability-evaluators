# A count is reported as it was stated, with its source, and is not checked against what was metered

Token usage is reported after execution, for each Attempt, each Evaluator and the sweep. A count that was not reported is missing with a reason, and nothing is guessed ([#22](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/22)).

The usage report gives the count of a use as it was stated to the agent target, and says where it comes from in `source`, when the target states that. For a Foundry hosted agent the source says that the count is the agent's own. The count is `reported`. It is not corrected, not replaced and not turned into a missing one, and totals add the reported counts whatever their source.

This follows from what was observed live on 2026-11-27 ([#52](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/52)). Foundry runs a prompt agent itself and reports the usage of its response, and that usage was what the model deployment metered, to the token, in every invocation that was looked at on its own. A hosted agent's own code produces its response, usage included, and Foundry passes it on, from customer code and through cloud evaluation. The usage the fixture's hosted agent stated was what the deployment metered with one model call, and 3.1 times that for the six model calls of the fixture's Task. The record is in the [documentation of token usage](../token_usage/readme.md#observed-live).

So there are counts that Foundry makes and counts that a customer's code makes, and the report cannot tell from the number which is right. It can say whose it is.

## Rejected

- **Reporting the count of a hosted agent as missing.** It is the only count there is for such an Attempt, it was right for a response with one model call, and another hosted agent may count correctly. The target invokes any hosted agent and cannot tell from outside how its code counts. Missing would say less than is known, and would hide what the platform returned.
- **Correcting the count.** The numbers fit a count that adds to every model call the tokens of the calls before it, exactly for two model calls and consistently for three and six. The response does not state its model calls one by one, so a correction would be a guess.
- **Reading the deployment's metrics instead.** They are per minute and per deployment. They cannot be attributed to an Attempt when anything else uses the deployment, they arrive minutes later, the identity that runs a sweep may not be allowed to read them, and whether they are what is billed was not established.
- **A total per source.** A sweep has one agent target, so its Attempts have one source. Nothing needs separate totals yet.

## Consequences

- The total of a sweep on a hosted agent is the sum of what the agent stated. For the fixture's agent that is about three times what the deployment metered. The scorecard, the report and the CI job summary say that the count is the agent's own, and the documentation says what was observed.
- Token usage of a prompt agent and of a hosted agent cannot be compared with each other.
- A customer whose hosted agent counts correctly loses nothing. A customer whose agent does not can see where to look.
- An agent target of the customer's own states the source of its counts in `token_usage_source`, or states none.
- The usage report is not a resource measurement. No Foundry agent target gives the sweep a measurement of tokens, so [resource consistency](../resource_consistency/readme.md) is not calculated from these counts.
- Not verified: why the fixture's agent stated more than was metered, what a hosted agent built with anything else states, and whether the metrics are what is billed. Reopen this if Foundry comes to report the usage of a hosted agent itself.
