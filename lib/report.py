"""detailed-report recording helpers. port of fixtures/report.ts + reporters/detailed.ts glue.

  - step()         wrap action so it shows as named ACTION with timing + pass/fail.
  - record()       attach JSON RECORD (fill, fee reading, snapshot) to test.
  - record_check() attach CHECK { name, pass, detail, info }. info check never mean failure.

all no-op toward test outcome. only enrich report. assertion still use `assert`.
conftest harvests the per-test buffer and writes results/runs/<runId>/detailed-report.md.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# per-test buffers. conftest clears before each test and harvests after.
_actions: list[dict] = []
_records: list[dict] = []
_checks: list[dict] = []
_notes: list[str] = []
_depth = 0


def reset() -> None:
    _actions.clear()
    _records.clear()
    _checks.clear()
    _notes.clear()


def harvest() -> dict:
    return {
        "actions": list(_actions),
        "records": list(_records),
        "checks": list(_checks),
        "notes": list(_notes),
    }


def step(name: str, body: Callable[[], T]) -> T:
    """group action into named, timed step. result returned unchanged."""
    global _depth
    start = time.monotonic()
    _depth += 1
    node = {"title": name, "durationMs": 0, "ok": True, "depth": _depth - 1}
    try:
        return body()
    except BaseException as err:
        node["ok"] = False
        node["error"] = str(err).split("\n")[0]
        raise
    finally:
        _depth -= 1
        node["durationMs"] = int((time.monotonic() - start) * 1000)
        _actions.append(node)


@contextmanager
def stepping(name: str):
    """context-manager flavor of step() for multi-statement blocks."""
    global _depth
    start = time.monotonic()
    _depth += 1
    node = {"title": name, "durationMs": 0, "ok": True, "depth": _depth - 1}
    try:
        yield
    except BaseException as err:
        node["ok"] = False
        node["error"] = str(err).split("\n")[0]
        raise
    finally:
        _depth -= 1
        node["durationMs"] = int((time.monotonic() - start) * 1000)
        _actions.append(node)


def record(name: str, data: Any) -> None:
    """attach structured record. shows under "Records" in detailed report."""
    _records.append({"name": name, "body": data})


def record_check(*, name: str, passed: bool, detail: Any = None, info: bool = False) -> None:
    """attach Check. shows under "Checks". does NOT fail test. pair with assert for gate."""
    _checks.append({"name": name, "pass": passed, "info": info, "detail": detail})


def annotate(note: str) -> None:
    """free-form note (port of test.info().annotations)."""
    _notes.append(note)
