"""A fake Azure CLI, first on PATH, for failing the live fixture's provisioning without Azure access.

It answers the commands that `live_fixture` runs up to the deployment of the to-do service, with
synthetic identifiers, and fails that deployment. A failing command repeats its whole command line in
its error, as a command-line tool may. Every command line it was run with is recorded.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
from pathlib import Path

# Shaped like a token of the Azure CLI: a header, claims that name the signed-in identity, a signature.
_CLAIMS = base64.urlsafe_b64encode(json.dumps({"oid": "synthetic-object-id"}).encode()).decode().rstrip("=")
TOKEN = f"eyJhbGciOiJub25lIn0.{_CLAIMS}.recognisable-fake-signature"
SUBSCRIPTION = "Sandbox"
_RESOURCE = "/subscriptions/synthetic-subscription/resourceGroups/synthetic/providers/Microsoft.CognitiveServices"

_SCRIPT = '''
import json, sys
from pathlib import Path

arguments = sys.argv[1:]
with (Path(__file__).parent / "command_lines.jsonl").open("a", encoding="utf-8") as recorded:
    recorded.write(json.dumps(arguments) + "\\n")


def starts_with(*words):
    return tuple(arguments[: len(words)]) == words


if starts_with("containerapp", "up"):
    sys.stderr.write("ERROR: the deployment failed: az " + " ".join(arguments) + "\\n")
    sys.exit(1)
if starts_with("account", "show"):
    answer = {"name": SUBSCRIPTION}
elif starts_with("account", "get-access-token"):
    answer = {"accessToken": TOKEN, "expires_on": 4102444800}
elif starts_with("group", "exists"):
    answer = False
elif starts_with("cognitiveservices", "account", "update"):
    answer = {"id": RESOURCE + "/accounts/synthetic", "identity": {"principalId": "synthetic-account-identity"}}
elif starts_with("cognitiveservices", "account", "project", "create"):
    answer = {
        "id": RESOURCE + "/accounts/synthetic/projects/todo-fixture",
        "identity": {"principalId": "synthetic-project-identity"},
    }
else:
    answer = {}
print(json.dumps(answer))
'''


class FakeAz:
    def __init__(self, directory: Path):
        self._directory = directory
        self.token = TOKEN

    def command_lines(self) -> list[list[str]]:
        """Every command line the fake was run with, in order, without the name of the program."""
        recorded = self._directory / "command_lines.jsonl"
        return [json.loads(line) for line in recorded.read_text(encoding="utf-8").splitlines()]

    def settings_passed(self, name: str) -> list[str]:
        """The values the fake was passed for a setting `NAME=value` on a command line."""
        return [
            argument.partition("=")[2]
            for arguments in self.command_lines()
            for argument in arguments
            if argument.startswith(name + "=")
        ]


def install(directory: Path, monkeypatch) -> FakeAz:
    """Put the fake in `directory` and that directory first on PATH, until the test ends."""
    script = directory / "fake_az.py"
    script.write_text(
        f"SUBSCRIPTION = {SUBSCRIPTION!r}\nTOKEN = {TOKEN!r}\nRESOURCE = {_RESOURCE!r}\n{_SCRIPT}", encoding="utf-8"
    )
    if os.name == "nt":
        (directory / "az.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = directory / "az"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return FakeAz(directory)
