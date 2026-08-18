"""one folder per test run: results/runs/run-NNNN-<ts>/.

NNNN = incremental (human order), <ts> suffix = unique (no collide on concurrent runs).
conftest mints it in the MAIN process + publishes via env + pointer file; xdist workers +
the reporter read it back so ALL output of one run lands in ONE folder. framework-agnostic.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "results"
RUNS = RESULTS / "runs"
POINTER = RESULTS / ".current-run"  # holds current run dir path. cross-process channel.

_cached: str | None = None


def _next_id() -> int:
    try:
        best = 0
        for name in os.listdir(RUNS):
            m = re.match(r"^run-(\d+)", name)
            if m:
                best = max(best, int(m.group(1)))
        return best + 1
    except OSError:
        return 1  # no runs dir yet


def _stamp() -> str:
    # 20260703-124032 utc. no colons/dots -> safe dir name.
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def create_run_context() -> tuple[str, str]:
    """MAIN process: mint a fresh run dir, publish to env + pointer + latest symlink."""
    global _cached
    run_id = f"run-{_next_id():04d}-{_stamp()}"
    run_path = RUNS / run_id
    run_path.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(str(run_path))
    os.environ["NIMBUS_RUN_DIR"] = str(run_path)
    os.environ["NIMBUS_RUN_ID"] = run_id
    # results/latest -> newest run. symlink best-effort.
    latest = RESULTS / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(Path("runs") / run_id, target_is_directory=True)
    except OSError:
        pass  # run dir still authoritative
    _cached = str(run_path)
    return run_id, str(run_path)


def run_dir() -> str:
    """ANY process (worker / reporter / adhoc): resolve current run dir.

    env (fast) -> pointer file (cross-process) -> adhoc standalone (lib used outside pytest).
    """
    global _cached
    if _cached:
        return _cached
    from_env = os.environ.get("NIMBUS_RUN_DIR")
    if from_env:
        _cached = from_env
        return _cached
    try:
        p = POINTER.read_text().strip()
        if p and Path(p).exists():
            _cached = p
            return _cached
    except OSError:
        pass
    adhoc = RUNS / f"adhoc-{_stamp()}-{os.getpid()}"
    adhoc.mkdir(parents=True, exist_ok=True)
    _cached = str(adhoc)
    return _cached
