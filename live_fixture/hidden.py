"""What the fixture keeps out of sight: its keys, its tokens, and what Azure answers with.

A failing live check prints its failure. A traceback shows the arguments and the local variables
of the functions it passes through, and an error shows what it was raised from. So a key or a
token is a `Secret` from the moment it exists, an answer of Azure is held in a container that
does not show what it holds, and an error of the standard library, whose own functions hold the
headers of the request, is never raised further and never chained to.
"""

import base64
import http.client
import json
import socket
import urllib.error
import urllib.request

NOT_SHOWN = "<not shown>"


class Secret:
    """A key, a token, or text that holds one. It shows itself in no `repr` and no `str`."""

    __slots__ = ("_text",)

    def __init__(self, text):
        self._text = text

    def __repr__(self):
        return NOT_SHOWN

    __str__ = __repr__

    def reveal(self):
        """The secret itself, for the request or the command line it is for."""
        return self._text

    def within(self, template):
        """Text around the secret, such as a header value or a setting, which is then a secret too."""
        return Secret(template.format(self._text))

    def claim(self, name):
        """A claim of a token, such as the object identifier of the identity, or None when it has none."""
        try:
            claims = self._text.split(".")[1]
            return json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4))).get(name)
        except (AttributeError, IndexError, ValueError):
            return None


def revealed(value):
    return value.reveal() if isinstance(value, Secret) else value


def scrubbed(text, secrets):
    """`text` without any of the secrets, should whatever answered have repeated one."""
    for secret in secrets:
        if isinstance(secret, Secret) and isinstance(secret.reveal(), str) and secret.reveal():
            text = text.replace(secret.reveal(), NOT_SHOWN)
    return text


class HiddenDict(dict):
    """An object that Azure answered with, or that is sent to it. It can hold keys and identifiers, so it is not shown."""

    def __repr__(self):
        return f"<an object of {len(self)} fields, not shown>"


class HiddenList(list):
    def __repr__(self):
        return f"<a list of {len(self)} items, not shown>"


class HiddenBytes(bytes):
    """The body of a request or of an answer."""

    def __repr__(self):
        return f"<{len(self)} bytes, not shown>"


def hidden(value):
    """`value` in a container that does not show what it holds, when it is one that can hold a secret."""
    for plain, container in ((dict, HiddenDict), (list, HiddenList), (bytes, HiddenBytes)):
        if type(value) is plain:
            return container(value)
    return value


class Unanswered(Exception):
    """A request got no answer. `never_sent` says that it provably did not leave."""

    def __init__(self, reason, never_sent):
        super().__init__(reason)
        self.reason = reason
        self.never_sent = never_sent


def send(method, url, content=None, headers=None, bearer=None, api_key=None, timeout=300):
    """Send one request, once. Returns the status of the answer and its body, whatever the status.

    `bearer` and `api_key` are Secrets, and `url` may be one. Raises Unanswered when no answer
    came back. Neither the answer nor the error holds a secret, and the error is chained to nothing.
    """
    request = urllib.request.Request(revealed(url), data=content, method=method, headers=dict(headers or {}))
    if bearer is not None:
        request.add_header("Authorization", bearer.within("Bearer {}").reveal())
    if api_key is not None:
        request.add_header("x-api-key", api_key.reveal())
    answer = unanswered = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answered:
            answer = answered.status, answered.read()
    except urllib.error.HTTPError as error:
        answer = error.code, error.read()
    except (OSError, http.client.HTTPException) as error:
        # Only text is kept of it: the error holds the frames of the standard library, and those the headers.
        reason = getattr(error, "reason", error)
        unanswered = Unanswered(
            scrubbed(f"{type(error).__name__}: {reason}", (bearer, api_key, url)),
            never_sent=isinstance(reason, socket.gaierror),
        )
        del reason
    # Raised here, after the handler, so that it is not chained to the error it describes.
    if unanswered is not None:
        raise unanswered
    return answer[0], HiddenBytes(scrubbed(answer[1].decode(errors="replace"), (bearer, api_key)).encode())
