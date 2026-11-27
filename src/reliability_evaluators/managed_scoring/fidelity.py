"""The fidelity rule: when a result recovered from Foundry-managed scoring is authoritative.

A managed result comes back on two channels, from separate calls of the same evaluator on the
same evidence:

- the plain channel: the number Foundry returns as the score of the row that asks for the
  metric value. It is what the customer's Foundry project shows as the metric.
- the encoded channel: the encoded result, in whole numbers of six bytes behind a digest. It
  holds the value as the Evaluator computed it, written so that it reads back to the same double.

The rule is applied to one result at a time. A result whose two channels agree is
authoritative. Any other is an unverified result. The reported value is always the plain one.

Agreement shows that the reported number is the one the Evaluator returned in Foundry. It does
not show that the calculation is right, and no calculation in the customer's own process takes
part in it. docs/managed_scoring/readme.md says what the rule rests on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..evaluators import Fidelity


@dataclass(frozen=True, kw_only=True)
class FidelityCheck:
    """The comparison of the two channels that one managed result came back on."""

    passed: bool
    # The number Foundry returned as the score, and the value the encoded result holds.
    plain_value: float | None
    encoded_value: float | None

    @property
    def fidelity(self) -> Fidelity:
        return Fidelity.AUTHORITATIVE if self.passed else Fidelity.UNVERIFIED

    @property
    def limitation(self) -> str | None:
        """Why the result is unverified, in words, or None for a check that passed."""
        if self.passed:
            return None
        return (
            f"Foundry returned {self.plain_value!r} as the value of this metric, and the encoded result that the "
            f"Evaluator returned from Foundry with it holds {self.encoded_value!r}. The two do not agree, so that "
            "the reported value equals the value the Evaluator computed there has not been established. It is "
            "reported as Foundry returned it, as an unverified result: it cannot be met or not met by an "
            "acceptance criterion. The evidence counts, the details and the Attempts are the ones in the encoded "
            "result, and belong to the value it holds."
        )

    def to_dict(self) -> dict[str, Any]:
        """The check as the scorecard records it, with what it establishes."""
        if self.encoded_value is None:
            meaning = NO_VALUE_AGREED
        else:
            meaning = VALUES_AGREE if self.passed else VALUES_DIFFER
        return {
            "outcome": "passed" if self.passed else "failed",
            "plain_value": self.plain_value,
            "encoded_value": self.encoded_value,
            "meaning": meaning,
        }


_ENCODED_RESULT = "the encoded result, which the Evaluator wrote in Foundry and which matched its digest"
VALUES_AGREE = (
    f"The number Foundry returned as the score equals the value in {_ENCODED_RESULT}. So the reported value is "
    "the one the Evaluator returned in Foundry for the submitted evidence, unchanged on the way back. This does "
    "not show that the calculation is right, and nothing was calculated in the customer's own process to compare "
    "it with."
)
VALUES_DIFFER = (
    f"The number Foundry returned as the score differs from the value in {_ENCODED_RESULT}. Which of the two the "
    "Evaluator computed is not established, and nothing was calculated in the customer's own process to decide "
    "between them."
)
NO_VALUE_AGREED = (
    f"Foundry returned the marker of a metric without a value as the score, and {_ENCODED_RESULT}, says that the "
    "metric has no value. So the two agree that there is none. The state, the reason and the evidence counts came "
    "back in the encoded result only: no second channel carries them."
)


NOT_CHECKED = (
    "Foundry returned this value, and it was not compared with the value in an encoded result. That it equals the "
    "value the Evaluator computed there has not been established, so it is an unverified result: it cannot be met "
    "or not met by an acceptance criterion."
)


def check_value(score: Any, encoded_value: float) -> FidelityCheck:
    """Compare the number Foundry returned as the score with the value the encoded result holds.

    They are compared as numbers, exactly: nothing is rounded, and adjacent doubles differ. A
    whole number that came back without a fraction equals itself, and so does a zero that
    came back without its sign, which is how Foundry returned both when observed.
    """
    return FidelityCheck(passed=score == encoded_value, plain_value=float(score), encoded_value=encoded_value)


def no_value_on_either_channel() -> FidelityCheck:
    """The check of a result without a value: the score was the marker for that, and the encoded result has none.

    A result whose channels disagree on whether there is a value is not recovered at all.
    """
    return FidelityCheck(passed=True, plain_value=None, encoded_value=None)
