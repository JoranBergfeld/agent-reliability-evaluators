# Foundry agent targets use the standard library and a token the caller provides

Customers copy the source and must be able to run it without installing anything ([ADR 0004](0004-copy-the-source-distribution.md)). The Foundry agent targets therefore call the project's REST interface with the Python standard library, and take the caller's identity as a function that returns a bearer token for `https://ai.azure.com`. By default that function asks the Azure CLI, which is how the customer's CI is already signed in to Azure. No Azure SDK is imported anywhere in the copyable source.

Decided while building the Foundry prompt agent target ([#43](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/43)). The prototypes had called Foundry the same way, and that is the only way that was ever run: installing `azure-ai-projects` failed during the prototypes, so no SDK call was validated. Foundry-managed scoring ([#46](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/46)), built at the same time, reaches the project the same way.

## Rejected

- **`azure-ai-projects` and `azure-identity` as dependencies of the agent target.** They would make every customer install and pin two packages and their dependencies to run a sweep, also customers who score in their own process and never use Foundry-managed scoring. No SDK call had been verified to work.
- **Reading the credential from environment variables or a file in this source.** The offering would then handle secrets. A token function leaves that to the customer's own tooling.
- **An API key.** Not accepted for agents, according to the research for the map. Not tried.

## Consequences

- A customer authenticates in CI by signing the Azure CLI in, for example with a federated credential, or by passing their own token function. A customer who prefers `azure-identity` passes `credential.get_token(...)` and carries that dependency themselves.
- The Azure CLI has to be on the path of the process that runs the sweep unless another token function is given.
- The request and response shapes of the REST interface are ours to track. What was observed, with the date and the API versions, is recorded in the documentation of each target.
- The canonical metric definitions and the evidence model import nothing from the agent target, so they stay pure whatever the targets need.
- The agent target and Foundry-managed scoring each have a `FoundryProject` of their own today, in `agent_target/foundry.py` and `managed_scoring/foundry.py`. They were built in parallel and differ: the agent target's returns the response headers, takes a time limit per request and never repeats a request. Making them one is open.
