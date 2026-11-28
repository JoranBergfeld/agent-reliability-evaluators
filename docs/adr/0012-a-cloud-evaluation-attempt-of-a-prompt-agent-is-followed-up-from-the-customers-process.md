# A cloud evaluation Attempt of a prompt agent is followed up from the customer's process

When Foundry cloud evaluation invokes the Attempts ([ADR 0007](0007-a-cloud-evaluation-run-invokes-one-attempt.md)), the confidence follow-up is still a request of the customer's own process. The cloud evaluation target asks a prompt agent exactly as the prompt agent target does ([ADR 0009](0009-prompt-agent-follow-up-rests-on-tool-choice-none.md)): one request to the project's Responses route that continues the response the output item of the Attempt names, names the agent and the version that item names, and carries `tool_choice: none`. It makes the same three checks of the answer and returns none unless all three hold. For a prompt agent the target declares the isolated follow-up supported. For a hosted agent it declares it unsupported, for the reason ADR 0009 gives. Until the project has said which of the two the agent is, it declares nothing supported.

Until this decision the target declared the follow-up unsupported because it had never been tried. It was tried on 2026-11-27 for the full-matrix verification ([#55](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/55)), against criteria that were written down before the first request: [the coverage matrix](../coverage_matrix/readme.md#the-confidence-follow-up-through-cloud-evaluation). The output item of a cloud evaluation run names a response of the project's own. A later request continued it: the project listed the Attempt's prompt and its five tool calls as the input of the answer. With the tool choice `none` the agent called nothing, four times of four, three of them while it was told to act. Without it, it acted.

## Rejected

- **Asking in a second cloud evaluation run.** Every run was seen to start a conversation of its own. Nothing was found by which a run names an earlier response or carries a tool choice, so the agent would answer without the Attempt before it, and with its tools.
- **Continuing the conversation the output item names, in place of the response.** Not tried. A response is what the prompt agent target continues, and the project says in its answer which response that was, which is one of the three checks.
- **Leaving it unsupported.** It was unsupported for want of verification and for no other reason. For a hosted agent the reason is another one, and that stays.

## Consequences

- Calibration, strict confidence discrimination and the Brier-based score can be scored for a prompt agent whose Attempts are invoked through cloud evaluation. They stay unavailable for a hosted agent, through either entry point.
- With a predictability metric selected, the process that runs the sweep sends a request to the agent itself. It then needs what the prompt agent target needs: access to the project's Responses route as an identity that may invoke the agent. Without such a metric, only Foundry invokes the agent.
- The follow-up is recorded on the Attempt as `confidence_invocation`, with the entry point `customer_code`. The Attempt keeps `foundry_cloud_evaluation`. Its usage has the field names of a response (`input_tokens`, `output_tokens`), where the Attempt's has those of an output item (`prompt_tokens`, `completion_tokens`).
- The isolation rests on what ADR 0009 says it rests on, with the same limits: the project applying the tool choice, for an OpenAPI tool and `gpt-4.1-mini`.
- `isolated_follow_up` of this target is not a constant. It is read from what the project said about the agent, which the target learns on its first Attempt or from `agent_configuration()`. The sweep reads it after an Attempt has completed.
- Not verified: an agent whose definition specifies a `tool_choice` of its own, after a cloud evaluation Attempt. From customer code the project refused such a follow-up.
