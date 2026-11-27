# Agent Reliability Evaluation

Vocabulary for measuring the reliability of customers' agents across tasks and experimental conditions.

## Language

**Reliability metric**:
A measurement of an aspect of agent consistency, robustness, predictability or operational safety. A metric value alone does not determine whether an agent is acceptable for a customer's use.

**Evaluator**:
A component that computes one reliability metric from evaluation evidence.

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

**Evaluator sweep**:
A coordinated evaluation of planned attempts across tasks and experimental conditions, bringing its results together in a reliability scorecard.

**Experiment**:
A task attempted under a baseline condition and at most one intended variation, with everything else held fixed. It is declared before any attempt is made.

**Experimental condition**:
The circumstances an attempt is made under: the baseline, or the one intended variation from it. The intended condition is what the experiment declared. The observed condition is what a customer control showed to have occurred. A label is not an observed condition.

**Prompt variant**:
A wording of a task's instruction that the customer supplies or approves as equivalent to the task's own, kept as its exact text with its source. It is judged against the same task-success definition. The offering generates none.
_Avoid_: Paraphrase, generated variant

**Starting-state policy**:
The declared rule for the state an attempt starts from: fresh, where the state is prepared before every attempt, or continuing, where each attempt deliberately starts from the state the previous one left.

**Customer control**:
An operation supplied by the customer that the evaluator sweep invokes at a defined point around an attempt: to prepare state, apply a condition, verify what occurred, or clean up.

**Task outcome**:
The success or failure of an attempt, determined against the customer's task-success definition, with its source recorded. It is not inferred from the agent's response alone and is distinct from deciding whether a reliability metric meets an acceptance criterion.
_Avoid_: Reliability acceptance

**Outcome correction**:
A task outcome supplied or corrected for one attempt after the run, with its source and its provenance. The attempt keeps the record that the correction replaced.

**Recalculation**:
Scoring the evidence of an earlier reliability scorecard again after an outcome correction. It gives a new scorecard and leaves the earlier one as it was.

**Superseded result**:
A metric result that a recalculation has replaced. It is kept, marked as superseded, in the scorecard that replaces it, and is not the current result.

**Execution status**:
How the running of an attempt or experiment ended, such as completed, timed out, cancelled or setup failed. It is separate from the task outcome and from the state of a reliability metric: an execution failure keeps its reason and is not a task failure, an unavailable measurement or not applicable.

**Invocation evidence**:
What an agent target recorded of the invocation of an attempt: the identifiers the platform returned, the token usage it reported and the tool calls it exposed. It is kept as returned, for a failed invocation too, and it never decides a task outcome. What a target recorded of the confidence request that followed an attempt is kept the same way, apart from the invocation.

**Token usage**:
The model tokens that were measured, after the run, for the attempts of an evaluator sweep, their confidence follow-ups, the scoring of its evaluators and the preparation a customer declares. It is reported per attempt, per evaluator and for the sweep, as metadata: it is not a reliability metric and no acceptance criterion reads it. Usage that was not reported is missing with a reason, never zero.
_Avoid_: Cost, budget, estimate

**Entry point**:
Where the request that invokes the agent for an attempt is sent from: customer code, in the customer's own process, or Foundry cloud evaluation, in an evaluation run of the customer's Foundry project. Each attempt records its entry point in its invocation evidence. It is independent of the scoring path, and on either one the evaluator sweep owns repetition, grouping and the customer controls.

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

**Not applicable**:
The state of a reliability metric whose protocol genuinely does not apply to the declared experiment, reported with a reason. It is distinct from an unavailable measurement and is never used for missing evidence or a failed experiment.

**Not configured**:
The state of a reliability metric that the customer did not select. It appears only in a reliability scorecard that groups several metrics, and is distinct from an unavailable measurement and from not applicable.

**Unverified result**:
A received metric value whose fidelity to the evaluator-computed value has not been established. It is distinct from an unavailable measurement.

**Scoring path**:
Where an evaluator's calculation runs: Foundry-managed, as a custom evaluator in Foundry, or customer-process, in the customer's own process. Each metric result records its scoring path, which is independent of where the attempts are invoked.

**Customer-process rescore**:
A scoring, in the customer's own process, of the evidence that Foundry-managed scoring was given for a metric, made only at the customer's explicit request. It is a separately attributed result, reported beside the Foundry-managed result or failure, which it never replaces.
_Avoid_: Fallback, automatic rescore
