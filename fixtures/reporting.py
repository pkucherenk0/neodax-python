"""per-test bookkeeping: artifacts (accounts/orders/trades saved during a test) + the
detailed markdown/JSON report written at session end.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from lib import report
from lib.artifacts import set_current_test
from lib.run_context import run_dir


@pytest.fixture(autouse=True)
def _artifacts(request: pytest.FixtureRequest):
    """auto per-test. stamp artifact records with the running test name. clear report buffer."""
    set_current_test(request.node.name)
    report.reset()
    yield
    set_current_test(None)


_test_reports: list[dict] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call":
        return
    detail = report.harvest()
    entry = {
        "suite": item.parent.name if item.parent else "",
        "title": item.name,
        "status": "passed" if rep.passed else ("skipped" if rep.skipped else "failed"),
        "durationMs": int(rep.duration * 1000),
        "markers": sorted({m.name for m in item.iter_markers()}),
        **detail,
    }
    if rep.failed and call.excinfo is not None:
        entry["error"] = "\n".join(str(call.excinfo.value).split("\n")[:4])
    _test_reports.append(entry)


def _safe_name(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:120]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """write results/runs/<runId>/detailed-report.md + detailed/<test>.json.
    per-worker under xdist: workers append their own tests."""
    if not _test_reports:
        return
    base = Path(run_dir())
    detailed = base / "detailed"
    detailed.mkdir(parents=True, exist_ok=True)
    for r in _test_reports:
        (detailed / f"{_safe_name(r['suite'] + '-' + r['title'])}.json").write_text(json.dumps(r, indent=2, default=str))

    def sec(ms: int) -> str:
        return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"

    icon = {"passed": "✅", "skipped": "⏭️", "failed": "❌"}
    out: list[str] = [f"# Detailed execution report", "", f"{len(_test_reports)} tests (worker pid {os.getpid()})", ""]
    for r in _test_reports:
        out.append(f"## {icon.get(r['status'], '?')} {r['suite']} › {r['title']}")
        tags = " · ".join(r["markers"])
        out.append(f"_{r['status']} · {sec(r['durationMs'])}{' · ' + tags if tags else ''}_")
        out.append("")
        if r["actions"]:
            out.append("**Actions**")
            for a in r["actions"]:
                pad = "  " * a.get("depth", 0)
                err = f" — {a['error']}" if a.get("error") else ""
                out.append(f"{pad}- {'✓' if a['ok'] else '✗'} {a['title']} _({sec(a['durationMs'])})_{err}")
            out.append("")
        if r["checks"]:
            out.append("**Checks**")
            for c in r["checks"]:
                mark = "ℹ️" if c["info"] else ("✓" if c["pass"] else "✗")
                detail_str = f"  `{json.dumps(c['detail'], default=str)}`" if c["detail"] is not None else ""
                out.append(f"- {mark} {c['name']}{detail_str}")
            out.append("")
        if r["records"]:
            out.append("**Records**")
            for rec in r["records"]:
                out.append(f"- `{rec['name']}`: `{json.dumps(rec['body'], default=str)}`")
            out.append("")
        if r["notes"]:
            out.append("**Notes**")
            out.extend(f"- {n}" for n in r["notes"])
            out.append("")
        if r.get("error"):
            out.extend(["**Error**", "```", r["error"], "```", ""])
        out.append("")
    # per-worker file under xdist, single file otherwise.
    suffix = f"-{os.getpid()}" if os.environ.get("PYTEST_XDIST_WORKER") else ""
    (base / f"detailed-report{suffix}.md").write_text("\n".join(out))
