"""Confidence evidence: the agent's confidence rating for one Attempt, or why there is none."""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .errors import EvidenceError

# A number written in plain decimal digits. Nothing else is read as a rating.
_NUMBER = re.compile(r"-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)")
# Far more digits than a rating has, and few enough to calculate with exactly.
MAXIMUM_RATING_LENGTH = 100


@dataclass(frozen=True, kw_only=True)
class ConfidenceRating:
    """How likely the agent thought it was, after the Attempt, that the task succeeded.

    `rating` is the number as the agent wrote it, on the scale 0 to 100. It stays text so
    that its recorded precision is kept: "87.50" is not turned into 87.5.
    `original_response` is the whole answer the rating was read from.
    """

    rating: str
    original_response: str

    def __post_init__(self) -> None:
        if not isinstance(self.rating, str):
            raise EvidenceError(
                f"a confidence rating is recorded as the agent wrote it, as text. Got {self.rating!r}"
            )
        if not _NUMBER.fullmatch(self.rating):
            raise EvidenceError(
                f"a confidence rating is one number in plain decimal digits. Got {self.rating!r}"
            )
        if len(self.rating) > MAXIMUM_RATING_LENGTH:
            raise EvidenceError(f"a confidence rating is not longer than {MAXIMUM_RATING_LENGTH} characters")
        if not 0 <= Fraction(self.rating) <= 100:
            raise EvidenceError(f"the confidence rating {self.rating!r} is outside 0 to 100")
        if not isinstance(self.original_response, str):
            raise EvidenceError("a confidence rating keeps the response it was read from")

    @property
    def exact(self) -> Fraction:
        """The rating on the scale 0 to 1, exactly. Metrics calculate with this."""
        return Fraction(self.rating) / 100

    @property
    def normalized(self) -> float:
        """The rating on the scale 0 to 1, as the double nearest to `exact`."""
        return float(self.exact)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rating": self.rating,
            "normalized": self.normalized,
            "original_response": self.original_response,
            "unavailable_reason": None,
        }


@dataclass(frozen=True, kw_only=True)
class ConfidenceUnavailable:
    """Why an Attempt has no confidence rating. It is never replaced by a number.

    `original_response` is what the agent answered, when it answered at all.
    """

    reason: str
    original_response: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise EvidenceError("an unavailable confidence rating records its reason")
        if self.original_response is not None and not isinstance(self.original_response, str):
            raise EvidenceError("an original confidence response is text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rating": None,
            "normalized": None,
            "original_response": self.original_response,
            "unavailable_reason": self.reason,
        }


EMPTY_RESPONSE = "the confidence response is empty"
NO_NUMBER = "no number in the confidence response"
SEVERAL_NUMBERS = "more than one number in the confidence response"
OTHER_TEXT = "the confidence response has text besides one number"
TOO_LONG = f"the number in the confidence response is longer than {MAXIMUM_RATING_LENGTH} characters"
OUT_OF_RANGE = "the rating is outside 0 to 100"

# The whole answer: one number, with nothing around it but whitespace and an optional percent sign.
_ONLY_A_NUMBER = re.compile(rf"\s*({_NUMBER.pattern})\s*%?\s*")


def read_confidence_response(response: str) -> ConfidenceRating | ConfidenceUnavailable:
    """Read the agent's answer to a confidence request as a rating.

    The answer is a rating when it is one number from 0 to 100 and nothing else. Anything
    else is unavailable with a reason: a number is never clamped, guessed, chosen from
    several, or lifted out of surrounding text.
    """
    if not isinstance(response, str):
        raise EvidenceError(f"a confidence response is the agent's answer as text. Got {response!r}")
    if not response.strip():
        return ConfidenceUnavailable(reason=EMPTY_RESPONSE, original_response=response)
    numbers = _NUMBER.findall(response)
    if not numbers:
        return ConfidenceUnavailable(reason=NO_NUMBER, original_response=response)
    if len(numbers) > 1:
        return ConfidenceUnavailable(reason=SEVERAL_NUMBERS, original_response=response)
    only_a_number = _ONLY_A_NUMBER.fullmatch(response)
    if not only_a_number:
        return ConfidenceUnavailable(reason=OTHER_TEXT, original_response=response)
    number = only_a_number.group(1)
    if len(number) > MAXIMUM_RATING_LENGTH:
        return ConfidenceUnavailable(reason=TOO_LONG, original_response=response)
    if not 0 <= Fraction(number) <= 100:
        return ConfidenceUnavailable(reason=OUT_OF_RANGE, original_response=response)
    return ConfidenceRating(rating=number, original_response=response)
