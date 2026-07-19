"""bounded condition polling — python stand-in for playwright's `expect.poll`.

CONVENTIONS §5: never sleep to "let things settle". wait for a CONDITION with a budget.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


def poll_until(
    read: Callable[[], T],
    predicate: Callable[[T], bool],
    *,
    timeout_s: float,
    intervals: Sequence[float] = (0.5, 1.0, 2.0),
    message: str = "",
) -> T:
    """poll `read` until `predicate(value)` or timeout. return last value.

    raises AssertionError with the last value on timeout, so the failure reads like
    an expect.poll failure (what we waited for + what we last saw).
    """
    deadline = time.monotonic() + timeout_s
    step = 0
    value = read()
    while not predicate(value):
        if time.monotonic() >= deadline:
            raise AssertionError(f"poll timed out after {timeout_s}s: {message} (last value: {value!r})")
        time.sleep(intervals[min(step, len(intervals) - 1)])
        step += 1
        value = read()
    return value


def wait_for_value(read: Callable[[], float], minimum: float, timeout_s: float, poll_s: float = 0.75) -> float:
    """poll numeric getter until >= minimum or timeout. return LAST value seen (never raises)."""
    deadline = time.monotonic() + timeout_s
    value = read()
    while value < minimum and time.monotonic() < deadline:
        time.sleep(poll_s)
        value = read()
    return value
