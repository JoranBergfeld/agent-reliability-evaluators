"""The customer's Foundry project, reached over its REST API as the customer's own identity.

This is the one place that sends a request to a Foundry project. The agent targets and
Foundry-managed scoring all go through a `FoundryProject`: it holds the caller's
credential, sends through `requests.send`, which shows the token nowhere, and knows the
one rule for repeating a request.

Standard library only: see ADR 0005. Nothing here holds an identity of its own.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from .credential import AzureCliCredential
from .requests import Answer, BearerToken, NotAnswered, send

# A token is asked for again after this long, well inside the hour it lasted when observed.
TOKEN_REUSE_SECONDS = 300.0
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


class FoundryRequestError(Exception):
    """A request to the Foundry project was not answered, or was answered with an error status.

    `status` is the HTTP status, or None when there was no answer at all.
    """

    def __init__(self, method: str, path: str, status: int | None, detail: str):
        answered = f"HTTP {status}" if status is not None else "no answer"
        super().__init__(f"{method} {path}: {answered}: {detail}".rstrip(": "))
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


class FoundryProject:
    """A Foundry project the customer has access to.

    `endpoint` is the project endpoint, `https://<account>.services.ai.azure.com/api/projects/<project>`.
    `credential` returns a bearer token for it, for the resource `https://ai.azure.com`. Without
    one the token comes from the Azure CLI, as `AzureCliCredential` gets it. Any function that
    returns such a token does, such as one built on `azure-identity` when the customer already
    uses it. Every request is made as the identity of that token.

    A token is never sent over plain HTTP, except to this machine, where a stand-in for a
    project can be run.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        credential: Callable[[], str] | None = None,
        timeout_seconds: float = 300.0,
    ):
        if not isinstance(endpoint, str) or not _may_be_sent_a_token(endpoint):
            raise ValueError(f"a Foundry project is reached at its https endpoint, got {endpoint!r}")
        self.endpoint = endpoint.rstrip("/")
        self._credential = credential or AzureCliCredential()
        self._timeout_seconds = timeout_seconds
        self._held: tuple[BearerToken, float] | None = None

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any:
        """Send one request to the project and return the JSON it answers with.

        `path` is relative to the project endpoint. A link the project returned is accepted
        as it is when it points into the same project. Raises FoundryRequestError when the
        project does not answer, or answers with an error status.

        A request is repeated only when it cannot have had an effect: it provably was not
        sent, or it only reads. One answered with HTTP 401 is sent once more, with a new token.
        """
        try:
            return self._answered(method, path, body, headers)
        except FoundryRequestError as error:
            if error.status != 401:
                raise
        # The token may have expired during a long run. One new token, one more try.
        self._held = None
        renew = getattr(self._credential, "renew", None)
        if callable(renew):
            renew()
        return self._answered(method, path, body, headers)

    def send_once(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Answer:
        """Send one request to the project, once, and return its answer whatever its status.

        For a request that invokes an agent: one that may have reached the agent is never
        repeated. Raises `NotAnswered` when no answer came back.
        """
        return send(
            method,
            self._url(path),
            bearer=self._bearer(),
            content=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout_seconds=self._timeout_seconds if timeout_seconds is None else timeout_seconds,
        )

    def sign_in(self) -> None:
        """Get the token now when none is held, so that the request that follows does not wait for it."""
        self._bearer()

    def _url(self, path: str) -> str:
        return path if path.startswith(self.endpoint + "/") else self.endpoint + path

    def _bearer(self) -> BearerToken:
        if self._held is None or time.monotonic() - self._held[1] > TOKEN_REUSE_SECONDS:
            # Held as a BearerToken from here on, which shows itself in no error and no traceback.
            self._held = (BearerToken(self._credential()), time.monotonic())
        return self._held[0]

    def _answered(self, method: str, path: str, body: Any, headers: dict[str, str] | None) -> Any:
        url = self._url(path)
        content = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
        for attempt in range(1, 6):
            try:
                answer = send(
                    method,
                    url,
                    bearer=self._bearer(),
                    content=content,
                    headers={"Content-Type": "application/json", **(headers or {})},
                    timeout_seconds=self._timeout_seconds,
                )
            except NotAnswered as unanswered:
                # Repeated only when it cannot have had an effect: it was never sent, or it only reads.
                if attempt == 5 or not (unanswered.never_sent or method == "GET"):
                    raise FoundryRequestError(method, path, None, unanswered.described) from None
                time.sleep(min(5.0 * 2 ** (attempt - 1), 60.0))
                continue
            if not 200 <= answer.status < 300:
                raise FoundryRequestError(method, path, answer.status, answer.text)
            return json.loads(answer.text) if answer.text else None


def _may_be_sent_a_token(endpoint: str) -> bool:
    address = urlsplit(endpoint)
    return address.scheme == "https" or (address.scheme == "http" and address.hostname in _LOOPBACK)
