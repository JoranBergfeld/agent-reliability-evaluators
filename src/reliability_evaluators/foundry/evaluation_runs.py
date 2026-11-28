"""What everything that makes an evaluation run in a Foundry project needs alike.

Foundry-managed scoring and the cloud evaluation target each make runs of their own kind and
wait for them in their own way. What a run has ended as and how its output items are paged
is the same for both, and is here.
"""

from __future__ import annotations

from typing import Any, Protocol

# The statuses of a run that has ended.
ENDED = ("completed", "failed", "canceled")
OUTPUT_ITEM_PAGE = 100


class Project(Protocol):
    """What is needed of a Foundry project to make runs in it. `FoundryProject` is the real one.

    `request` returns the JSON the project answers with, and raises `FoundryRequestError` when
    it is not answered or answered with an error status.
    """

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any: ...


def output_items_of(project: Project, run_path: str) -> list[dict[str, Any]]:
    """Every output item of a run, page after page, in the order the project returns them.

    What is no page or no item is left out: the caller checks that it got the items it expects.
    """
    items: list[dict[str, Any]] = []
    after = ""
    while True:
        page = project.request("GET", f"{run_path}/output_items?limit={OUTPUT_ITEM_PAGE}{after}")
        page = page if isinstance(page, dict) else {}
        items.extend(item for item in page.get("data") or [] if isinstance(item, dict))
        if not page.get("has_more") or not page.get("last_id"):
            return items
        after = f"&after={page['last_id']}"
