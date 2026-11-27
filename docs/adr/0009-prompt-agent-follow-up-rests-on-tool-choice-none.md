# The confidence follow-up of a prompt agent rests on the project applying the tool choice none

After each Attempt the agent is asked for its confidence in a separate request, in which it must not be able to act. If that cannot be ensured there is no rating, and no fallback to a rating in the task response ([#16](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/16)). A prompt instruction that asks the agent not to act does not ensure it.

For a Foundry prompt agent, the target asks in a request that continues the response of the Attempt and carries `tool_choice: none`, and declares the isolated follow-up supported on that basis. For a Foundry hosted agent, the target declares it unsupported.

The tool choice is a setting of the request that the project applies when it runs a prompt agent. It is not something the agent is asked to do. Relying on it was verified live on 2026-11-27 ([#53](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/53)): an agent that was told to act in such a follow-up called nothing, every time, and acted every time the tool choice was left out. Microsoft documents the setting as "the model doesn't call tools". The project refuses the request, before the agent runs, for an agent whose definition specifies a tool choice of its own. It was not seen to ignore the setting. The target also reads from each response which response it continued, which tool choice it ran under and which tools were called, and returns no answer unless all three are as they should be.

That is not a proof. The observations show that no call got through when the agent tried. That none ever can rests on what the platform documents the setting to do, for the tool types and the one model that were tried. The record is in the [documentation of the prompt agent target](../foundry_prompt_agent/readme.md#the-confidence-follow-up).

A hosted agent is the customer's code. The project hands it the request, and that code decides whether a tool is called. Two hosted agents that differ in one line of their own code were sent the same follow-up with `tool_choice: none`: one called nothing and the other acted. So no request ensures isolation there.

## Rejected

- **An empty `tools` list in the request.** The project accepted it, ignored it, and the agent called its tool.
- **Asking the agent's model deployment directly**, in a request that continues the response of the Attempt and carries the agent's instructions and no tool. It worked when tried, and such a request has no tool that could be called, so nothing would rest on a setting being applied. Rejected because it is then not the evaluated agent that answers: the target would rebuild the agent from its definition, would have to carry over every setting of it, and the caller would need access to the deployment.
- **A second prompt agent without tools** that answers in the conversation of the Attempt. It worked when tried. Rejected because the customer would have to create that agent and keep it in step with the evaluated one, and the answer would again come from another agent.
- **Declaring the follow-up unsupported for a prompt agent as well**, on the reading that only a request without any tool ensures isolation. Rejected because the tool choice is documented, was not once seen to let a call through, and is refused rather than ignored where the project will not apply it. Reopen this if a tool type or a model is found for which the setting does not hold.
- **Declaring it supported for a hosted agent whose code was seen to honour the tool choice.** The fixture's agent did. The target invokes any hosted agent and cannot tell from outside which kind it is invoking.

## Consequences

- Calibration, strict confidence discrimination and the Brier-based score can be scored for a prompt agent invoked from customer code, and are unavailable for a hosted agent, with the reason.
- The isolation of a prompt agent's follow-up is as strong as the platform's enforcement of the tool choice. The target's check of each response discards an answer given after a tool call. It cannot undo the call.
- A prompt agent whose definition specifies a `tool_choice` has no rating: the project runs it only under that tool choice.
- A customer who owns a hosted agent's code and can vouch that it cannot act in a follow-up can write an agent target that declares the isolated follow-up. The declaration is then the customer's.
- Not verified: tool types other than an OpenAPI tool and the code interpreter, a tool the platform runs without a tool call of the model, and models other than `gpt-4.1-mini`.
- This is about agents invoked from customer code. Whether the same request can follow an Attempt that Foundry cloud evaluation invoked ([ADR 0007](0007-a-cloud-evaluation-run-invokes-one-attempt.md)) was open when this was decided. It was tried later and can: [ADR 0011](0011-a-cloud-evaluation-attempt-of-a-prompt-agent-is-followed-up-from-the-customers-process.md).
