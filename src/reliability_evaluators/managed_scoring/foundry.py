"""The customer's Foundry project, reached over its REST API as the customer's own identity.

Standard library only: requests go through `urllib` with a bearer token from a function
the customer chooses. Nothing here stores a token, and nothing holds an identity of its own.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Callable

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
    result = subprocess.run(command, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise FoundryRequestError("az", "account get-access-token", None, result.stderr.strip())
    return json.loads(result.stdout)["accessToken"]


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
        self._held: tuple[str, float] | None = None

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

    def _bearer(self, renew: bool = False) -> str:
        if renew or self._held is None or time.monotonic() - self._held[1] > TOKEN_REUSE_SECONDS:
            self._held = (self._token(), time.monotonic())
        return self._held[0]

    def _send(self, method: str, path: str, body: Any, headers: dict[str, str] | None, bearer: str) -> Any:
        url = path if path.startswith(self.endpoint + "/") else self.endpoint + path
        request = urllib.request.Request(
            url,
            data=None if body is None else json.dumps(body, allow_nan=False).encode("utf-8"),
            method=method,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {bearer}", **(headers or {})},
        )
        for attempt in range(1, 6):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                    content = response.read()
                    return json.loads(content) if content else None
            except urllib.error.HTTPError as error:
                raise FoundryRequestError(method, path, error.code, error.read().decode(errors="replace")) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                # Repeated only when it cannot have had an effect: it was never sent, or it only reads.
                never_sent = isinstance(getattr(error, "reason", None), socket.gaierror)
                if attempt == 5 or not (never_sent or method == "GET"):
                    raise FoundryRequestError(method, path, None, f"{type(error).__name__}: {error}") from None
                time.sleep(min(5.0 * 2 ** (attempt - 1), 60.0))
