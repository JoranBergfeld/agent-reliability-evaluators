"""The caller's identity for a Foundry project: a function that returns a bearer token.

Everything that reaches a Foundry project takes it under the one name `credential`, and
uses the Azure CLI when none is given. The offering reads no secret from the environment or
from a file: see ADR 0005.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time

# What a token for a Foundry project is issued for.
TOKEN_RESOURCE = "https://ai.azure.com"


class AzureCliCredential:
    """Bearer tokens for a Foundry project, from the identity the Azure CLI is signed in with.

    In CI that is whatever the pipeline signed in as, such as a service principal or a
    federated workload identity. The Azure CLI has to be installed; nothing else is needed.

    Calling it returns a token. The token is kept until five minutes before it expires, so a
    long sweep asks the Azure CLI again only when it has to. `subscription` names the
    subscription to ask for, when the signed-in identity has more than one tenant.
    """

    def __init__(self, subscription: str | None = None):
        self._subscription = subscription
        self._token: str | None = None
        self._expires_on = 0.0

    def __call__(self) -> str:
        if self._token is None or time.time() >= self._expires_on - 300:
            az = shutil.which("az")
            if az is None:
                raise RuntimeError("the Azure CLI (az) is not on PATH, so it cannot give a token for the Foundry project")
            command = [az, "account", "get-access-token", "--resource", TOKEN_RESOURCE, "--only-show-errors", "-o", "json"]
            if self._subscription:
                command += ["--subscription", self._subscription]
            failed = self._ask(command)
            if failed is not None:
                raise RuntimeError(f"the Azure CLI gave no token for the Foundry project: {failed}")
        return self._token

    def renew(self) -> None:
        """Forget the token that is kept, so that the next call asks the Azure CLI again."""
        self._token = None

    def _ask(self, command: list[str]) -> str | None:
        """Ask the Azure CLI for a token and keep it. Returns why it gave none, or None when it gave one.

        What the Azure CLI printed holds the token. It is read here and goes no further, so that
        it is no variable of a function an error is raised in.
        """
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            return f"it did not run to its end ({type(error).__name__})"
        if result.returncode:
            return result.stderr.strip()
        try:
            token = json.loads(result.stdout)
            # Without an expiry time the token is not kept.
            self._token, self._expires_on = token["accessToken"], float(token.get("expires_on") or 0)
        except (ValueError, KeyError, TypeError):
            return "it answered with something that holds no access token"
        return None
