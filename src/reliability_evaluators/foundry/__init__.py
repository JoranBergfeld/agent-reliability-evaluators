"""Requests to the customer's Foundry project, sent as the customer's own identity.

Standard library only. `requests` sends one request with a bearer token that is shown nowhere:
not in an error, not in what an error is chained to, and not in what a traceback holds.
"""

from .requests import Answer, BearerToken, NotAnswered, send

__all__ = ["Answer", "BearerToken", "NotAnswered", "send"]
