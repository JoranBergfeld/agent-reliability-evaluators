"""One request to a Foundry project, with a bearer token that is shown nowhere.

A token is a credential. A traceback shows the arguments and the local variables of the
functions it passes through, and an error shows what it was raised from. So a token is held
in a `BearerToken`, which does not show itself, from the moment it is obtained, and an error
of the standard library, whose own functions hold the request headers, is never raised
further and never chained to: what `send` raises was made here and holds text only.
"""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping

NOT_SHOWN = "<bearer token, not shown>"


class BearerToken:
    """A bearer token. It shows itself in no `repr` and no `str`, so in no traceback and no message."""

    __slots__ = ("_text",)

    def __init__(self, text: Any):
        self._text = text

    def __repr__(self) -> str:
        return NOT_SHOWN

    __str__ = __repr__

    def authorization(self) -> str:
        """The value of the Authorization header. It is for the request only."""
        return f"Bearer {self._text}"

    def scrubbed(self, text: str) -> str:
        """`text` without the token, should whatever answered have repeated it."""
        token = self._text
        return text.replace(token, NOT_SHOWN) if isinstance(token, str) and token else text


@dataclass(frozen=True)
class Answer:
    """An HTTP answer, whatever its status. Its body is not part of its `repr`: a body can hold settings."""

    status: int
    text: str = field(repr=False)
    # The headers of the answer, by their names in lower case.
    headers: Mapping[str, str] = field(repr=False, default_factory=dict)

    @property
    def json(self) -> Any:
        """The body as JSON, or None when it is not JSON."""
        try:
            return json.loads(self.text)
        except ValueError:
            return None

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class NotAnswered(Exception):
    """A request got no answer, so nothing is known about what it did.

    `reason` is why, in words. `described` is the same with the kind of error in front.
    `timed_out` says that the wait for the answer ended, and `never_sent` that the request
    provably did not leave: the name of the host could not be resolved.
    """

    def __init__(self, reason: str, described: str, *, timed_out: bool, never_sent: bool):
        super().__init__(described)
        self.reason = reason
        self.described = described
        self.timed_out = timed_out
        self.never_sent = never_sent


def send(
    method: str,
    url: str,
    *,
    bearer: BearerToken,
    content: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float,
) -> Answer:
    """Send one request, once, and return its answer whatever its status.

    Raises NotAnswered when no answer came back. Neither the answer nor the error holds the
    token, and the error is chained to nothing.
    """
    request = urllib.request.Request(url, data=content, method=method, headers=dict(headers or {}))
    request.add_header("Authorization", bearer.authorization())
    answer = unanswered = None
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as answered:
            answer = _answer(answered.status, answered, bearer)
    except urllib.error.HTTPError as error:
        answer = _answer(error.code, error, bearer)
    except (OSError, http.client.HTTPException) as error:
        # URLError is an OSError. Only text is kept of it: the error itself holds the frames of
        # the standard library, and those hold the headers of the request.
        reason = getattr(error, "reason", error)
        unanswered = NotAnswered(
            bearer.scrubbed(str(reason)),
            bearer.scrubbed(f"{type(error).__name__}: {error}"),
            timed_out=isinstance(reason, TimeoutError),
            never_sent=isinstance(reason, socket.gaierror),
        )
        del reason
    # Raised here, after the handler, so that it is not chained to the error it describes.
    if unanswered is not None:
        raise unanswered
    return answer


def _answer(status: int, answered: Any, bearer: BearerToken) -> Answer:
    return Answer(
        status,
        bearer.scrubbed(answered.read().decode(errors="replace")),
        {name.lower(): bearer.scrubbed(value) for name, value in answered.headers.items()},
    )
