# One canonical metric definition, with Foundry-managed artifacts derived from it

Every reliability metric can be scored on two scoring paths, customer-process and Foundry-managed, and the metrics carry fidelity conventions where the paper leaves gaps. Each metric therefore has one authoritative definition of its semantics. The Foundry-specific evaluator artifacts are derived from that definition and verified for parity with customer-process scoring, so that the two scoring paths cannot drift apart.

Decided in [Choose distribution and packaging of the offering](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/21).

## Rejected

- **A separately maintained implementation for each scoring path.** Ruled out by the decision itself. The ticket asked how metric definitions and fidelity conventions stay consistent across the artifacts, and the answer was one definition with the rest derived from it.
- **Dropping a scoring path so that only one implementation is needed.** Both scoring paths stay in scope. Leaving the Foundry registration mechanics to later implementation work does not descope Foundry-managed scoring.

## Consequences

- The canonical definition has to run wherever the derived artifact runs. The [spec](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/25) assumes the artifacts are Foundry code evaluators, which limits the canonical definitions to that sandbox: no network and a fixed package list. That is an assumption of the spec, not part of this decision.
- Parity between a derived artifact and its canonical definition is checked, not assumed.
