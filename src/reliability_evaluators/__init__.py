"""Agent reliability evaluators. Copy this directory into your project.

Modules:

- `evaluators`: the canonical metric definitions and the metric result contract.
- `evidence`: the Attempt record and the grouping Evaluators are called with.
- `sweep`: the Evaluator sweep and what a customer declares for it.
- `agent_target`: the interface the sweep invokes an agent through, for an Attempt and for the confidence follow-up.
- `scorecard`: the machine-readable reliability scorecard, the human-readable report and their delivery.
- `managed_scoring`: scoring as a custom evaluator in the customer's Foundry project, derived from the canonical definition.
"""
