"""Reads a human-readable report back, so that it can be compared with the scorecard it renders.

A report marks every scorecard value it shows with where that value is in the scorecard:
`data-scorecard` holds a JSON Pointer (RFC 6901). A field name is marked with its key in
`data-key` and shown unchanged. Everything else a reader sees is the report's own wording.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Iterator

_VOID = {"meta", "br", "hr", "img", "input", "link", "source", "embed"}


class Report(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        # Scorecard values in the order they are shown: (pointer, text).
        self.values: list[tuple[str, str]] = []
        self.classes: dict[str, set[str]] = {}
        # For a shown value: the in-page link it is wrapped in, and the ids of the elements it is inside.
        self.links: dict[str, str] = {}
        self.inside: dict[str, list[str]] = {}
        self.keys: list[tuple[str, str]] = []
        self.wording = ""
        self.article_wording: dict[str, str] = {}
        self.article_classes: dict[str, set[str]] = {}
        self.styles = ""
        self._open: list[tuple[str, dict[str, str | None]]] = []
        self._marked: tuple[list[tuple[str, str]], str, list[str]] | None = None
        self.feed(html)
        self.close()

    def value(self, pointer: str) -> str:
        """The one text the report shows for the scorecard value at `pointer`."""
        (text,) = [text for shown_at, text in self.values if shown_at == pointer]
        return text

    def ids(self) -> list[str]:
        return [attributes["id"] for _, attributes in self.elements if attributes.get("id")]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        assert self._marked is None, f"<{tag}> inside the marked text of {self._marked[1]}"
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag in _VOID:
            return
        self._open.append((tag, attributes))
        classes = set((attributes.get("class") or "").split())
        if tag == "article":
            self.article_wording[attributes["id"]] = ""
            self.article_classes[attributes["id"]] = classes
        if "data-scorecard" in attributes:
            pointer = attributes["data-scorecard"]
            self.classes[pointer] = classes
            self.inside[pointer] = [enclosing["id"] for _, enclosing in self._open if enclosing.get("id")]
            links = [enclosing["href"] for name, enclosing in self._open if name == "a"]
            if links:
                self.links[pointer] = links[-1]
            self._marked = (self.values, pointer, [])
        elif "data-key" in attributes:
            self._marked = (self.keys, attributes["data-key"], [])

    def handle_endtag(self, tag: str) -> None:
        if self._marked is not None:
            into, mark, text = self._marked
            into.append((mark, "".join(text)))
            self._marked = None
        opened, _ = self._open.pop()
        assert opened == tag, f"</{tag}> closes <{opened}>"

    def handle_data(self, data: str) -> None:
        enclosing = [tag for tag, _ in self._open]
        if "style" in enclosing or "script" in enclosing:
            self.styles += data
        elif self._marked is not None:
            self._marked[2].append(data)
        else:
            self.wording += data
            for tag, attributes in self._open:
                if tag == "article":
                    self.article_wording[attributes["id"]] += data


def assert_faithful_rendering(html: str, scorecard: dict[str, Any]) -> Report:
    """The report shows every value of the scorecard where the scorecard has it, and no other value."""
    report = Report(html)
    recorded = dict(_leaves(scorecard, ""))
    for pointer, text in report.values:
        assert pointer in recorded, f"the report shows {text!r} at {pointer}, which the scorecard does not have"
        assert text == _as_shown(recorded[pointer]), f"{pointer} is {recorded[pointer]!r} and is shown as {text!r}"
    left_out = set(recorded) - {pointer for pointer, _ in report.values}
    assert not left_out, f"the report leaves out {sorted(left_out)}"
    numbers = re.findall(r"[^.\n]*\d[^.\n]*", report.wording)
    assert not numbers, f"the report's own wording carries numbers: {numbers}"
    keys = set(_keys(scorecard))
    for key, label in report.keys:
        assert key in keys, f"the report names a field {key!r} that the scorecard does not have"
        assert label == key
    return report


def _as_shown(value: Any) -> str:
    """How a report shows a value: strings as they are, numbers in full, and a dash for no value."""
    if value is None:
        return "\N{EM DASH}"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return repr(value)
    assert value in ([], {})
    return "none"


def _leaves(value: Any, pointer: str) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from _leaves(child, f"{pointer}/{key.replace('~', '~0').replace('/', '~1')}")
    elif isinstance(value, list) and value:
        for position, child in enumerate(value):
            yield from _leaves(child, f"{pointer}/{position}")
    else:
        yield pointer, value


def _keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)
