"""The customer's Foundry project, reached over its REST API as the customer's own identity.

Standard library only: requests go through `urllib` with a bearer token from a function
the customer chooses. Nothing here stores a token, and nothing holds an identity of its own.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any, Callable

from ..foundry import BearerToken, NotAnswered, send

# What a token for a Foundry project is issued for.
TOKEN_RESOURCE = "https://ai.azure.com"
# A token is asked for again after this long, well inside the hour it lasted when observed.
TOKEN_REUSE_SECONDS = 300.0


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


def azure_cli_token() -> str:
    """A bearer token for the identity that is signed in to the Azure CLI.

    In CI that is whatever the pipeline signed in as, such as a service principal or a
    federated workload identity. The Azure CLI has to be installed; nothing else is needed.
    """
    az = shutil.which("az")
    if az is None:
        raise FoundryRequestError("az", "account get-access-token", None, "the Azure CLI (az) is not installed")
    command = [az, "account", "get-access-token", "--resource", TOKEN_RESOURCE, "--only-show-errors", "-o", "json"]
    failed, answered = _ran(command)
    if failed is not None:
        raise FoundryRequestError("az", "account get-access-token", None, failed)
    return answered


def _ran(command: list[str]) -> tuple[str | None, Any]:
    """Why the Azure CLI gave no token, or None and the token it gave.

    What the Azure CLI printed holds the token. It is read here and goes no further, so that
    it is no variable of a function an error is raised in.
    """
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        return f"the Azure CLI did not run to its end: {type(error).__name__}", None
    if result.returncode:
        return result.stderr.strip(), None
    try:
        return None, json.loads(result.stdout)["accessToken"]
    except (ValueError, KeyError, TypeError):
        return "the Azure CLI answered with something that holds no access token", None


class FoundryProject:
    """A Foundry project the customer has access to.

    `endpoint` is the project endpoint, `https://<account>.services.ai.azure.com/api/projects/<project>`.
    `token` returns a bearer token for it. By default that is the Azure CLI's; any function
    that returns a token for `https://ai.azure.com` does, such as one built on `azure-identity`
    when the customer already uses it. Every request is made as the identity of that token.
    """

    def __init__(
        self, endpoint: str, *, token: Callable[[], str] = azure_cli_token, timeout_seconds: float = 300.0
    ):
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise ValueError(f"a Foundry project is reached at its https endpoint, got {endpoint!r}")
        self.endpoint = endpoint.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._held: tuple[BearerToken, float] | None = None

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any:
        """Send one request to the project and return the JSON it answers with.

        `path` is relative to the project endpoint. A link the project returned is accepted
        as it is when it points into the same project.
        """
        try:
            return self._send(method, path, body, headers, self._bearer())
        except FoundryRequestError as error:
            if error.status != 401:
                raise
        # The token may have expired during a long run. One new token, one more try.
        return self._send(method, path, body, headers, self._bearer(renew=True))

    def _bearer(self, renew: bool = False) -> BearerToken:
        if renew or self._held is None or time.monotonic() - self._held[1] > TOKEN_REUSE_SECONDS:
            # Held as a BearerToken from here on, which shows itself in no error and no traceback.
            self._held = (BearerToken(self._token()), time.monotonic())
        return self._held[0]

    def _send(
        self, method: str, path: str, body: Any, headers: dict[str, str] | None, bearer: BearerToken
    ) -> Any:
        url = path if path.startswith(self.endpoint + "/") else self.endpoint + path
        content = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
        for attempt in range(1, 6):
            try:
                answer = send(
                    method,
                    url,
                    bearer=bearer,
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
