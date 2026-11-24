# Agent Reliability Evaluation

Vocabulary for measuring the reliability of customers' agents across tasks and experimental conditions.

## Language

**Reliability metric**:
A measurement of an aspect of agent consistency, robustness, predictability or operational safety. A metric value alone does not determine whether an agent is acceptable for a customer's use.

**Reliability scorecard**:
A presentation of individual reliability metrics without a combined reliability score or universal acceptance thresholds.
_Avoid_: Overall reliability score, composite score

**Acceptance criterion**:
A customer-defined condition that determines whether a measured reliability result is acceptable for their use.
_Avoid_: Task outcome

**Task**:
A unit of work defined by a customer for an agent to perform.

**Attempt**:
One execution of an agent on a task. Outcomes and other evaluation evidence are associated with individual attempts.

**Experiment protocol**:
The declared task, task-success rule, agent configuration, starting-state policy and conditions governing a set of attempts and their comparison.

**Experimental condition**:
The circumstances under which an attempt is performed, including a baseline or declared variation. The intended condition is distinct from evidence of the condition actually encountered.

**Starting-state policy**:
The declared relationship between attempts' initial conversation and external state, such as fresh or reset state versus deliberately continuing state. A fresh conversation does not imply that external state has been reset.

**Invalid experiment**:
An experiment whose execution does not meet its declared protocol, such as failed setup or failure to establish a required condition. This is distinct from an agent failing its task under a valid experimental condition.

**Task outcome**:
The success or failure of an attempt, determined against the customer's task-success definition, with its source recorded. It is not inferred from the agent's response alone and is distinct from deciding whether a reliability metric meets an acceptance criterion.
_Avoid_: Reliability acceptance

**Confidence rating**:
The evaluated agent's estimate, given after an attempt, of its confidence in the task outcome.

**Operational safety**:
Adherence to declared operational constraints and the bounded severity of their violation. This is distinct from treating content safety as a substitute for operational consequences.

**Operational constraint**:
A customer-defined safety rule that applies to a task and is used to assess the agent's execution.

**Violation assessment**:
A determination of whether an attempt violated an applicable operational constraint, supported by evidence and, where applicable, a severity rating.

**Severity rating**:
An assessment of how serious a specific constraint violation is.

**Unavailable measurement**:
A metric result that cannot be validly calculated from the available evidence. It is reported with a reason and evidence count, not replaced with an invented numeric value.
