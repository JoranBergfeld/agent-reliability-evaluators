# Agent Reliability Evaluation

This context describes evidence used to measure the reliability of a customer's agent on its tasks.

## Language

**Task**:
A unit of work defined by a customer for an agent to perform.

**Attempt**:
One execution of an agent on a task. Outcomes and other evaluation evidence are associated with individual attempts.

**Task outcome**:
The customer's determination of whether an attempt succeeded. The outcome's source is recorded; it is not inferred from the agent's response alone.

**Confidence rating**:
The evaluated agent's estimate, given after an attempt, of its confidence in the task outcome.

**Operational constraint**:
A customer-defined safety rule that applies to a task and is used to assess the agent's execution.

**Violation assessment**:
A determination of whether an attempt violated an applicable operational constraint, supported by evidence and, where applicable, a severity rating.

**Severity rating**:
An assessment of how serious a specific constraint violation is.

**Unavailable measurement**:
A metric result that cannot be validly calculated from the available evidence. It is reported with a reason and evidence count, not replaced with an invented numeric value.
