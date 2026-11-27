# Customers copy the source; no published package

Research found no channel for a third party to publish Foundry evaluators to customers, and recommended a Python package plus a registration step in each customer's project. For the first version no package is published. The evaluator source stays in this public repository, and customers copy it into their own project, identify its version by commit or tag, and call it from their own CI.

The ticket records the decision without the reasoning for preferring copied source to a package. The [spec](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/25) states what the customer gets from it: a pinned commit or tag shows exactly which metric definitions produced a result, and the Evaluators fit the pipeline the customer already has.

Decided in [Choose distribution and packaging of the offering](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/21), which supersedes the package recommendation in [Establish distribution channels for Foundry custom evaluators and CI/CD](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/20).

## Rejected

- **A published Python package, on PyPI or a private feed.** Recommended by the research and superseded.
- **A first-party GitHub Action, Azure DevOps task or workflow template.** Not provided initially. Customers integrate the source into their own CI.
- **Requiring customers to use uv.** uv is development tooling for this repository.

## Consequences

- A result is tied to a commit or tag of this repository, not to a package version.
- Foundry-managed scoring still needs the evaluators registered in the customer's own Foundry project. The registration mechanics were left to implementation work.
