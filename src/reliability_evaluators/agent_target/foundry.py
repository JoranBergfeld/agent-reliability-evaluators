"""What every Foundry agent target shares: the project, the caller's identity and the reply of a call.

Standard library only: plain HTTPS to the project's REST interface, with a bearer token of
the caller's own identity. No Azure SDK is needed or used.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..evidence import ActionEvent, PlatformEvidence, ResourceMeasurement, ResourceType
from .interface import AgentResponse, InvocationError

# The audience of the tokens a Foundry project accepts.
FOUNDRY_TOKEN_RESOURCE = "https://ai.azure.com"

# How much of an error body that is not in the expected shape is kept in the reason of a failure.
_ERROR_BODY_LIMIT = 500
TOOL_CALL_UNIT = "calls"
TOOL_CALL_BOUNDARY = "tool calls in the output of the Foundry response, counted by the agent target"

# Returns a bearer token for FOUNDRY_TOKEN_RESOURCE. Called before every request.
TokenProvider = Callable[[], str]


def _run_azure_cli(arguments: Sequence[str]) -> str:
    """Run `az` with the arguments and return what it printed, as JSON text."""
    az = shutil.which("az")
    if az is None:
        raise RuntimeError("the Azure CLI (az) was not found. Install it and sign in, or give another token provider")
    result = subprocess.run([az, *arguments, "--output", "json"], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"az {' '.join(arguments[:2])} failed: {result.stderr.strip()}")
    return result.stdout


class AzureCliToken:
    """A token provider that asks the Azure CLI for a token of the identity signed in to it.

    A token is reused until it is within `refresh_margin_seconds` of the expiry the CLI
    gave for it, and then asked for again, so a sweep can outlast a token. A token the
    CLI gives no expiry for is not reused. The token is held in memory only.

    `run_cli` runs `az` with the arguments it is given and returns its JSON output. It
    raises when the CLI gives no token. `clock` gives the time in seconds since the epoch.
    """

    def __init__(
        self,
        *,
        resource: str = FOUNDRY_TOKEN_RESOURCE,
        refresh_margin_seconds: float = 300.0,
        run_cli: Callable[[Sequence[str]], str] = _run_azure_cli,
        clock: Callable[[], float] = time.time,
    ):
        self._resource = resource
        self._refresh_margin_seconds = refresh_margin_seconds
        self._run_cli = run_cli
        self._clock = clock
        self._token: str | None = None
        self._expires_on = 0.0

    def __call__(self) -> str:
        if self._token is None or self._clock() >= self._expires_on - self._refresh_margin_seconds:
            issued = json.loads(self._run_cli(["account", "get-access-token", "--resource", self._resource]))
            self._token = issued["accessToken"]
            expires_on = issued.get("expires_on")
            self._expires_on = float(expires_on) if isinstance(expires_on, (int, float)) else 0.0
        return self._token


@dataclass(frozen=True)
class FoundryReply:
    """What the project answered to one request: the HTTP status, the headers and the body, as received."""

    status: int
    headers: Mapping[str, str]
    content: bytes

    def header(self, name: str) -> str | None:
        """The value of a header, whatever the case of its name."""
        return next((value for key, value in self.headers.items() if key.lower() == name.lower()), None)

    def json(self) -> Any:
        """The body as JSON data, or None when it is not JSON."""
        try:
            return json.loads(self.content)
        except ValueError:
            return None


class FoundryProject:
    """A Foundry project, called over its REST interface as the caller's own identity.

    `endpoint` is the project endpoint, `https://<account>.services.ai.azure.com/api/projects/<project>`.
    `token` returns a bearer token for `https://ai.azure.com`. It is called before every
    request, so it decides how long a token is reused. Without one, the identity signed in
    to the Azure CLI is used: see AzureCliToken.
    """

    def __init__(self, endpoint: str, token: TokenProvider | None = None):
        if not isinstance(endpoint, str) or not endpoint.startswith(("https://", "http://")):
            raise ValueError(f"a Foundry project is identified by its endpoint, an https URL. Got {endpoint!r}")
        self.endpoint = endpoint.rstrip("/")
        self._token = token if token is not None else AzureCliToken()

    def post(self, path: str, body: Mapping[str, Any], timeout_seconds: float) -> FoundryReply:
        """Send one request and return the reply, whatever its HTTP status. It is never sent twice.

        Raises TimeoutError when no reply arrived within `timeout_seconds`. A request that
        could not be sent, or whose reply could not be read, raises what the transport raised.
        """
        request = urllib.request.Request(
            self.endpoint + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token()}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return FoundryReply(response.status, dict(response.headers), response.read())
        except urllib.error.HTTPError as error:
            return FoundryReply(error.code, dict(error.headers), error.read())
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise _timed_out(timeout_seconds) from error
            raise
        except (TimeoutError, socket.timeout) as error:
            raise _timed_out(timeout_seconds) from error


def _timed_out(timeout_seconds: float) -> TimeoutError:
    return TimeoutError(f"the Foundry project did not answer within {timeout_seconds:g} seconds")


def read_responses_reply(reply: FoundryReply, source: str) -> AgentResponse:
    """What the sweep keeps of the reply to one call of the Responses interface.

    `source` says which interface was called and from where. The identifiers, the token
    usage and the tool calls are kept as the project returned them.

    Raises InvocationError, with what the project returned, when the invocation failed:
    the project answered with an HTTP error status, the body is not a response, or the
    response did not complete. A response that arrives is not by that alone a completed one.
    """
    response = reply.json()
    if reply.status != 200:
        raise _rejected(reply, response, source)
    if not isinstance(response, Mapping) or not isinstance(response.get("output"), list):
        raise _not_a_response(reply, source)
    output = [item for item in response["output"] if isinstance(item, Mapping)]
    tool_calls = _tool_calls(output)
    usage = response.get("usage")
    evidence = PlatformEvidence(
        source=source,
        identifiers=_identifiers(reply, response),
        token_usage=usage if isinstance(usage, Mapping) else None,
        token_usage_missing=None if isinstance(usage, Mapping) else "the response carried no usage",
        tool_calls=tool_calls,
    )
    if response.get("status") != "completed" or response.get("error"):
        raise InvocationError(
            f"the Foundry response {response.get('id')} ended with the status {response.get('status')!r}: "
            f"{_why_not_completed(response)}",
            platform_evidence=evidence,
        )
    return AgentResponse(
        output="".join(
            part.get("text", "")
            for item in output
            if item.get("type") == "message"
            for part in item.get("content", [])
            if isinstance(part, Mapping) and part.get("type") == "output_text"
        ),
        resource_measurements=(
            ResourceMeasurement(
                resource_type=ResourceType.TOOL_CALLS,
                value=len(tool_calls),
                unit=TOOL_CALL_UNIT,
                boundary=TOOL_CALL_BOUNDARY,
            ),
        ),
        platform_evidence=evidence,
    )


def _why_not_completed(response: Mapping[str, Any]) -> str:
    error, incomplete = response.get("error"), response.get("incomplete_details")
    if isinstance(error, Mapping):
        return f"{error.get('code')}: {error.get('message')}"
    if isinstance(incomplete, Mapping) and incomplete.get("reason"):
        return str(incomplete["reason"])
    return "the response gives no reason"


def _not_a_response(reply: FoundryReply, source: str) -> InvocationError:
    identifiers = {
        "http_status": reply.status,
        "request_id": reply.header("x-request-id"),
        "apim_request_id": reply.header("apim-request-id"),
    }
    return InvocationError(
        f"the Foundry project answered HTTP {reply.status} with a body that is not a response: "
        f"{reply.content.decode(errors='replace')[:_ERROR_BODY_LIMIT]}",
        platform_evidence=PlatformEvidence(
            source=source,
            identifiers={name: value for name, value in identifiers.items() if value},
            token_usage_missing="the Foundry project returned no response, so no usage",
        ),
    )


def _rejected(reply: FoundryReply, body: Any, source: str) -> InvocationError:
    """The failure of an invocation the project answered with an HTTP error status."""
    error = body.get("error") if isinstance(body, Mapping) else None
    error = error if isinstance(error, Mapping) else {}
    additional = error.get("additionalInfo")
    identifiers = {
        "http_status": reply.status,
        "error_code": error.get("code"),
        "error_request_id": error.get("request_id")
        or (additional.get("request_id") if isinstance(additional, Mapping) else None),
        "request_id": reply.header("x-request-id"),
        "apim_request_id": reply.header("apim-request-id"),
    }
    described = " ".join(str(part) for part in (reply.status, error.get("code")) if part)
    message = error.get("message") or reply.content.decode(errors="replace")[:_ERROR_BODY_LIMIT]
    return InvocationError(
        f"the Foundry project answered HTTP {described}: {message}",
        platform_evidence=PlatformEvidence(
            source=source,
            identifiers={name: value for name, value in identifiers.items() if value},
            token_usage_missing=f"the Foundry project answered HTTP {reply.status} and reported no usage",
        ),
    )


def _identifiers(reply: FoundryReply, response: Mapping[str, Any]) -> dict[str, Any]:
    """The identifiers the project returned with a response. One it did not return is left out."""
    returned = {
        "response_id": response.get("id"),
        "status": response.get("status"),
        "agent_reference": response.get("agent_reference"),
        "model": response.get("model"),
        "served_model": reply.header("x-ms-served-model"),
        "metadata": response.get("metadata"),
        "request_id": reply.header("x-request-id"),
        "apim_request_id": reply.header("apim-request-id"),
    }
    return {name: value for name, value in returned.items() if value}


def _tool_calls(output: list[Mapping[str, Any]]) -> tuple[ActionEvent, ...]:
    """The tool calls among the output items of a response, in the order of the response.

    A tool call is an item whose type ends in `_call`, such as `openapi_call`. What it
    returned is the item of the type `<that type>_output` with the same `call_id`.
    """
    returned = {
        item.get("call_id"): item.get("output")
        for item in output
        if str(item.get("type", "")).endswith("_call_output")
    }
    return tuple(
        ActionEvent(
            tool_name=item.get("name") or item["type"],
            arguments=item.get("arguments"),
            result=returned.get(item.get("call_id")),
            error=None if item.get("status") in (None, "completed") else f"the call ended with status {item['status']!r}",
        )
        for item in output
        if str(item.get("type", "")).endswith("_call")
    )
