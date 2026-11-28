"""Everything a failure shows of itself, as one text, to search for what must never be shown.

A token or a key can leave a process in an error message, in the `repr` of something an
error holds, in what an error was raised from, and in a traceback, which can show the
arguments and the local variables of every function it passes through. pytest prints the
arguments of the function a failure was raised in, and every local variable with `--showlocals`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent


def shown_by(error: BaseException) -> str:
    """What the error, everything it is chained to and their tracebacks hold, and what pytest prints of them.

    The functions of a test hold the secret they test with, so the variables of a function
    in `tests/` are left out. Those of the offering, the fixture and the standard library are not.
    """
    shown: list[str] = []
    seen: set[int] = set()
    chained = [error]
    while chained:
        current = chained.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        shown += [type(current).__name__, str(current), repr(current), repr(getattr(current, "__dict__", {}))]
        traceback = current.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            if not _in_a_test(frame.f_code.co_filename):
                shown += [f"{frame.f_code.co_name}: {name} = {value!r}" for name, value in frame.f_locals.items()]
            traceback = traceback.tb_next
        # Also what the error was raised from and during, whether or not it is suppressed.
        chained += [current.__cause__, current.__context__]
    # As pytest prints a failing test, with the arguments of each function and its local variables.
    printed = pytest.ExceptionInfo.from_exception(error).getrepr(
        showlocals=True,
        style="long",
        funcargs=True,
        truncate_locals=False,
        truncate_args=False,
        chain=True,
        tbfilter=lambda raised: raised.traceback.filter(lambda entry: not _in_a_test(str(entry.path))),
    )
    return "\n".join([*shown, str(printed)])


def _in_a_test(filename: str) -> bool:
    return _TESTS in Path(filename).resolve().parents
