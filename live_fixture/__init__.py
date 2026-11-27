"""Disposable live verification fixture: one to-do service, a Foundry prompt agent and a hosted agent.

See README.md. `provision`, `connect` and `teardown` need the Azure CLI; the rest does not.
"""

from .provisioning import LiveFixture, LiveFixtureError, connect, provision, teardown
from .task import TASK_PROMPT, TASK_STEPS, strict_task_outcome, task_prompt
from .todo_service import TodoService, TodoServiceError, local_service