# Foundry is called with the standard library and a token the caller provides

Customers copy the source and must be able to run it without installing anything ([ADR 0004](0004-copy-the-source-distribution.md)). Everything in the copyable source that talks to a Foundry project therefore calls the project's REST interface with the Python standard library, and takes the caller's identity as a function that returns a bearer token for `https://ai.azure.com`. By default that function asks the Azure CLI, which is how a customer's CI is usually signed in to Azure already. No Azure SDK is imported anywhere in the copyable source.

The same choice was made three times, independently and at the same time: for the Foundry prompt agent target ([#43](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/43)), the Foundry hosted agent target ([#44](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/44)) and Foundry-managed scoring ([#46](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/46)). The prototypes had called Foundry the same way, and that is the only way that was ever run: installing `azure-ai-projects` failed during the prototypes, so no SDK call was validated.

## Rejected

- **`azure-ai-projects` and `azure-identity` as dependencies.** They would make every customer install and pin two packages and their dependencies to run a sweep, also customers who score in their own process and never use Foundry-managed scoring. No SDK call had been verified to work.
- **Reading the credential from environment variables or a file in this source.** The offering would then handle secrets. A token function leaves that to the customer's own tooling.
- **An API key.** Not accepted for agents, according to the research for the map. Not tried.

## Consequences

- A customer authenticates in CI by signing the Azure CLI in, for example with a federated credential, or by passing their own token function. A customer who prefers `azure-identity` passes `credential.get_token(...)` and carries that dependency themselves.
- The Azure CLI has to be on the path of the process that runs the sweep unless another token function is given.
- The request and response shapes of the REST interface are ours to track. What was observed, with the date and the API versions, is recorded in the documentation of each target and of managed scoring.
- The canonical metric definitions and the evidence model import nothing that talks to Foundry, so they stay pure.
- Requests to a Foundry project are sent from three places today: `managed_scoring/foundry.py`, `agent_target/foundry_hosted.py` and `agent_target/foundry_prompt_agent.py`. The two agent targets share the Azure CLI credential and the reading of a response's output. Managed scoring has a token function and a client of its own, which repeats a request that provably was not sent and renews its token once on HTTP 401; the agent targets never repeat a request. Making them one is open.
