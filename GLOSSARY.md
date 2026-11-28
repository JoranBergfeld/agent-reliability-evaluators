# Agent Reliability Evaluation

Vocabulary for measuring the reliability of customers' agents across tasks and experimental conditions.

## Language

**Reliability metric**:
A measurement of an aspect of agent consistency, robustness, predictability or operational safety. A metric value alone does not determine whether an agent is acceptable for a customer's use.

**Evaluator**:
A component that computes one reliability metric from evaluation evidence.

**Derived evaluator**:
The custom evaluator in the customer's Foundry project that is derived from the canonical definition of one evaluator, for Foundry-managed scoring. It runs that same definition, so the two scoring paths have one definition of each metric.

**Reliability scorecard**:
A presentation of individual reliability metrics without a combined reliability score or universal acceptance thresholds.
_Avoid_: Overall reliability score, composite score

**Acceptance criterion**:
A customer-defined condition that determines whether a measured reliability result is acceptable for their use.
_Avoid_: Task outcome

**Acceptance assessment**:
The outcome of assessing a metric result against the customer's acceptance criterion: met, not met, or not assessed with a reason. A metric without a criterion has none, and a result that is unavailable, not applicable or unverified is not assessed.

**Task**:
A unit of work defined by a customer for an agent to perform.

**Task-success rule**:
The customer's own rule that decides the task outcome of an attempt, from their tests, labels or checks. It looks at independent evidence, such as the state of a service, and not at the agent's response alone.
_Avoid_: Task-success definition

**Attempt**:
One execution of an agent on a task. Outcomes and other evaluation evidence are associated with individual attempts.

**Evaluator sweep**:
A coordinated evaluation of planned attempts across tasks and experimental conditions, bringing its results together in a reliability scorecard.

**Agent target**:
The interface through which the evaluator sweep invokes an agent, for an attempt and for the confidence follow-up. There is one for a Foundry prompt agent and one for a Foundry hosted agent, each reachable from customer code and through Foundry cloud evaluation.

**Set of attempts**:
One making of every attempt that an evaluator sweep plans. Evaluators are scored from the same set when what they need the attempts to be made with is the same. A further set is made for an evaluator that needs something else, and attempts of different sets are never scored together.

**Experiment**:
A task attempted under a baseline condition and at most one intended variation, with everything else held fixed. It is declared before any attempt is made, with its task-success rule, agent configuration and starting-state policy.

**Experimental condition**:
The circumstances an attempt is made under: the baseline, or the one intended variation from it. The intended condition is what the experiment declared. The observed condition is what a customer control showed to have occurred. A label is not an observed condition.

**Kind of variation**:
What a variation changes: a fault of a tool, the environment, which is an equivalent presentation of the same tools and data, or the prompt. A robustness metric compares the baseline with variations of its own kind.

**Prompt variant**:
A wording of a task's instruction that the customer supplies or approves as equivalent to the task's own, kept as its exact text with its source. It is judged against the same task-success rule. The offering generates none.
_Avoid_: Paraphrase, generated variant

**Starting-state policy**:
The declared rule for the state an attempt starts from: fresh, where the state is prepared before every attempt, or continuing, where each attempt deliberately starts from the state the previous one left.

**Customer control**:
An operation supplied by the customer that the evaluator sweep invokes at a defined point around an attempt: to prepare state, apply a condition, verify what occurred, or clean up.

**Execution limit**:
A limit the customer sets on the making of a task's attempts: how long one attempt may take, how long all of them may take together, and how often an attempt whose invocation failed is retried. None is imposed by default.
_Avoid_: Budget

**Task outcome**:
The success or failure of an attempt, determined against the customer's task-success rule, with its source recorded. It is not inferred from the agent's response alone and is distinct from deciding whether a reliability metric meets an acceptance criterion.
_Avoid_: Reliability acceptance

**Outcome correction**:
A task outcome supplied or corrected for one attempt after the run, with its source and its provenance. The attempt keeps the record that the correction replaced.

**Recalculation**:
Scoring the evidence of an earlier reliability scorecard again after an outcome correction. It gives a new scorecard and leaves the earlier one as it was.

**Superseded result**:
A metric result that a recalculation has replaced. It is kept, marked as superseded, in the scorecard that replaces it, and is not the current result.

**Execution status**:
How the running of an attempt or experiment ended, such as completed, timed out, cancelled or setup failed. It is separate from the task outcome and from the state of a reliability metric: an execution failure keeps its reason and is not a task failure, an unavailable measurement or not applicable.

**Cut short**:
The attempts that a metric is calculated from were cut short when not all the planned ones were made, or not one of them completed. A metric that cannot be calculated from attempts that were cut short has an execution failure and no result. It is not an unavailable measurement.

**Scorecard status**:
Whether a reliability scorecard holds everything the evaluator sweep planned to collect and score: complete, partial with what is missing and why, or reporting failed. It is reported apart from the state of each metric and from the execution status of each attempt.

**Reporting failure**:
The explicit failure a caller is given when the reliability scorecard cannot be produced or delivered. Evidence that was collected is kept where that is safely possible, and a missing report is never read as a clean one.

**Invocation evidence**:
What an agent target recorded of the invocation of an attempt: the identifiers the platform returned, the token usage it reported and the tool calls it exposed. It is kept as returned, for a failed invocation too, and it never decides a task outcome. What a target recorded of the confidence request that followed an attempt is kept the same way, apart from the invocation.

**Action evidence**:
The actions an agent took in an attempt, in order: for each, the tool name, the arguments, the result and the error, with the source they were recorded from. A recording that is missing is not a confirmed empty sequence of actions.

**Action type**:
What a customer declares a tool name to be, for the trajectory metrics, which compare action types and nothing else of an action. The mapping from tool names to action types and the source of the action evidence are declared by the customer and have no default.

**Resource measurement**:
How much of one resource an attempt used: elapsed time, tool calls or token usage, with its unit and its measurement boundary. A measurement that was not made is missing, never zero.

**Measurement boundary**:
What a resource measurement covers: where it starts and stops, what it includes and who measured it. Measurements with different boundaries are not compared.

**Token usage**:
The model tokens that were measured, after the run, for the attempts of an evaluator sweep, their confidence follow-ups, the scoring of its evaluators and the preparation a customer declares. It is reported per attempt, per evaluator and for the sweep, as metadata: it is not a reliability metric and no acceptance criterion reads it. Usage that was not reported is missing with a reason, never zero.
_Avoid_: Cost, budget, estimate

**Entry point**:
Where the request that invokes the agent for an attempt is sent from: customer code, in the customer's own process, or Foundry cloud evaluation, in an evaluation run of the customer's Foundry project. Each attempt records its entry point in its invocation evidence. It is independent of the scoring path, and on either one the evaluator sweep owns repetition, grouping and the customer controls.

**Confidence rating**:
The evaluated agent's own estimate, given in an isolated follow-up after an attempt, of how likely it thinks the task succeeded. It is not how certain the agent is of a predicted outcome, and it is not an independently verified probability. A rating that is missing, unreadable, ambiguous or out of range is unavailable with a reason, and is never invented, clamped or asked for again.

**Isolated follow-up**:
A separate request put to the agent after a completed attempt, in the conversation of that attempt, in which the agent cannot take any action and is not shown the task outcome. The confidence rating is asked for in one. Where an agent target cannot ensure the isolation, the attempt has no confidence rating.

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

**Fidelity**:
Whether a metric value is known to equal the value its evaluator computed: authoritative, or an unverified result. It is independent of the state of the metric.

**Authoritative**:
The fidelity of a metric value that is known to equal the evaluator-computed value: one scored in the customer's own process, or one received from Foundry that passed its fidelity check. Only an authoritative value can be met or not met by an acceptance criterion.

**Scoring path**:
Where an evaluator's calculation runs: Foundry-managed, as a custom evaluator in Foundry, or customer-process, in the customer's own process. Each metric result records its scoring path, which is independent of where the attempts are invoked.

**Customer-process rescore**:
A scoring, in the customer's own process, of the evidence that Foundry-managed scoring was given for a metric, made only at the customer's explicit request. It is a separately attributed result, reported beside the Foundry-managed result or failure, which it never replaces.
_Avoid_: Fallback, automatic rescore
