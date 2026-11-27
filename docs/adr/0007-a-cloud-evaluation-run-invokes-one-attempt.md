# A Foundry cloud evaluation run invokes one Attempt

Foundry cloud evaluation is the second entry point for the Attempts of an Evaluator sweep, beside customer code. A cloud evaluation run takes rows of data and invokes the agent once per row. The Evaluator sweep makes one run with one row for every Attempt and every retry, and waits for it to end before it does anything else for that Attempt. To the sweep, cloud evaluation is an agent target like any other: `invoke` makes the run, waits for it and reads its one output item.

This follows from [the offering owning attempt coordination](0001-offering-owned-attempt-coordination.md). The customer controls run around each Attempt: state is prepared and the condition applied before it, the condition is verified and the state cleaned up after it. A run with several rows gives the sweep no point between two rows at which to run a control. Which row the agent is invoked for first, and how many at once, is then Foundry's to decide. So is what a failed row does to the others.

Decided while building the entry point ([#45](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/45)).

## Rejected

- **One run for all Attempts of a sweep, a Task or a condition.** It would cost one run's overhead instead of one per Attempt, about 25 seconds each when observed. The verify control could no longer say which Attempt a fault occurred in, fresh state could not be prepared between Attempts, a time limit or a retry would apply to the whole batch, and cancellation could not stop before the next Attempt. Repetition and order would be Foundry's.
- **Several rows in one run only where no control runs between them**, such as the Attempts of a Task given without an experiment. That would be a second way of executing Attempts, with other semantics for time limits, retries and cancellation, for the Tasks whose state is least controlled. It would also make the run's `max_concurrency` a setting the offering relies on, which was not established.
- **A separate way of running a sweep for cloud evaluation, outside the agent target interface.** The lifecycle, the execution limits, the retries and the evidence grouping would have to be built a second time, and the two entry points could come to differ in what an execution status means.

## Consequences

- Sequential by default holds as it does from customer code: the next run is made only after the one before has ended and the controls of its Attempt have run. With `max_concurrent_attempts` above 1, the Attempts of Tasks given without an experiment are several runs at once. That was not tried.
- The starting-state policy is followed by the sweep, not by Foundry. A run is a new conversation and resets nothing outside it. Under continuing state the Attempts are still separate runs.
- Each Attempt pays the overhead of a run. A time limit declared for an Attempt covers the whole run, not only the agent's answer.
- A run's counts and usage rows describe one Attempt. Nothing is summed or grouped from them.
- Foundry does not invoke the agent without a testing criterion, so every run carries one that is no judge and decides nothing. What Foundry labels with it is a platform diagnostic.
- Nothing here rules out a later batch mode. It would need Foundry to guarantee the order of rows and to offer a hook between them, neither of which was observed.
