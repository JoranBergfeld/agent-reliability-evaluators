# The offering owns attempt coordination and evidence grouping

The reliability metrics need repeated Attempts under controlled experimental conditions, with the evidence grouped by Task and condition. The Evaluator sweep does that coordination and grouping itself, as components the customer runs in their own code or CI. Foundry executes the agent and can score evidence that has already been grouped. It is not relied on to repeat Attempts, group evidence, reset state or inject faults, because neither the prototypes nor the documentation consulted established that it does.

Decided in [Choose agent integration and controlled-experiment boundaries](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/6).

## Rejected

- **Foundry-managed repetition and grouping where supported, with customer-side coordination only where needed.** This was the preferred direction when the decision was reopened. The prototypes then scored evidence that had been grouped before it reached Foundry. Neither showed Foundry repeating Attempts or reducing across rows.
- **The customer runs the Attempts and supplies all trials.** Set aside in the first discussion on the ticket: the offering connects to the agent and collects the repeated Attempts.
- **A separately hosted evaluation service that coordinates the Attempts.** The offering is components used in the customer's own workflow.

## Consequences

- Choosing the Foundry-managed scoring path moves only the calculation into Foundry. Repetition and grouping stay with the Evaluator sweep on both scoring paths.
- The customer supplies the controls that prepare state, apply a condition, verify what occurred and clean up. A fresh conversation is not proof that external state was reset.
- Native Foundry coordination is a possible later improvement, not a prerequisite. That it was not verified is not proof that it is unsupported.
