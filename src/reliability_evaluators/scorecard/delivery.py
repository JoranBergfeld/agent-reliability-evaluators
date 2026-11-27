"""Delivery of the scorecard, the report and the evidence to a location the caller chooses.

The location is a directory in the run or artifact context of the platform the caller runs
on. That platform governs access, retention and deletion. Nothing is kept anywhere else.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

from .assemble import SCHEMA_VERSION, ScorecardStatus, to_json
from .report import render_report
from .summary import render_summary

EVIDENCE_FILE = "evidence.json"
SCORECARD_FILE = "scorecard.json"
REPORT_FILE = "report.html"
# The text for the customer's pipeline to publish as its job summary.
SUMMARY_FILE = "summary.md"
REPORTING_FAILURE_FILE = "reporting-failure.json"


class ReportingFailure(Exception):
    """The scorecard or the report could not be produced or delivered. No result was delivered.

    `retained` names the files that were kept at the location. `evidence` is the attempt-level
    evidence that existed and `scorecard` is the scorecard, if one was produced, so that the
    caller can still keep them when the location could not.
    """

    def __init__(
        self,
        message: str,
        *,
        retained: tuple[str, ...] = (),
        evidence: list[dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.retained = retained
        self.evidence = evidence
        self.scorecard = scorecard


class ArtifactLocation:
    """A directory that receives `evidence.json`, `scorecard.json`, `report.html` and `summary.md`.

    Opening it removes those files, and the record of a reporting failure, when an earlier
    run left them there: what the location holds afterwards is from this run. A reporting
    failure leaves `reporting-failure.json` and no report.
    """

    def __init__(self, directory: str | os.PathLike[str]):
        self._directory = Path(directory)
        self._retained: list[str] = []
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            for name in (EVIDENCE_FILE, SCORECARD_FILE, REPORT_FILE, SUMMARY_FILE, REPORTING_FAILURE_FILE):
                (self._directory / name).unlink(missing_ok=True)
        except OSError as error:
            raise ReportingFailure(
                f"reporting failed: {self._directory} cannot receive the scorecard, the report and the evidence "
                f"({_named(error)})"
            ) from error

    def deliver(self, scorecard: dict[str, Any]) -> None:
        """Write the evidence, then the scorecard, then the CI job summary and the report rendered from it.

        The first one that cannot be produced or written is a ReportingFailure, and what
        comes after it is not written. The report is last, so a reporting failure leaves none.
        """
        artifacts: tuple[tuple[str, Callable[[], str]], ...] = (
            (EVIDENCE_FILE, lambda: _evidence_json(scorecard["attempts"])),
            (SCORECARD_FILE, lambda: to_json(scorecard)),
            (SUMMARY_FILE, lambda: render_summary(scorecard)),
            (REPORT_FILE, lambda: render_report(scorecard)),
        )
        for name, produce in artifacts:
            try:
                self._write(name, produce())
            except Exception as error:
                evidence = scorecard.get("attempts") if isinstance(scorecard, dict) else None
                failure = self.failed(
                    f"{name} could not be produced or written", error, evidence=evidence, scorecard=scorecard
                )
                raise failure from error

    def failed(
        self,
        what: str,
        error: BaseException,
        *,
        evidence: list[dict[str, Any]] | None,
        scorecard: dict[str, Any] | None = None,
    ) -> ReportingFailure:
        """Record a reporting failure at the location and return it, for the caller to raise.

        Evidence that is not at the location yet is written there first, if it can be.
        """
        if evidence and EVIDENCE_FILE not in self._retained:
            with suppress(Exception):
                self._write(EVIDENCE_FILE, _evidence_json(evidence))
        retained = tuple(self._retained)
        record = {
            "schema_version": SCHEMA_VERSION,
            "status": ScorecardStatus.REPORTING_FAILED.value,
            "failure": what,
            "error": _named(error),
            "retained": list(retained),
        }
        with suppress(Exception):
            self._write(REPORTING_FAILURE_FILE, to_json(record))
        if retained:
            kept = f"Kept in {self._directory}: {', '.join(retained)}."
        else:
            kept = f"Nothing was kept in {self._directory}."
        return ReportingFailure(
            f"reporting failed: {what} ({_named(error)}). {kept}",
            retained=retained,
            evidence=evidence,
            scorecard=scorecard,
        )

    def _write(self, name: str, text: str) -> None:
        path = self._directory / name
        try:
            path.write_text(text, encoding="utf-8", newline="\n")
        except BaseException:
            # A file that was not written whole is not left behind to be read as a result.
            with suppress(OSError):
                path.unlink()
            raise
        self._retained.append(name)


def _evidence_json(attempts: list[dict[str, Any]]) -> str:
    return to_json({"schema_version": SCHEMA_VERSION, "attempts": attempts})


def _named(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"
