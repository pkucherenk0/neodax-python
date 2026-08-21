"""save minted account creds to a git-ignored JSONL, one file per run -- so a failed run leaves
a trace of which throwaway account was involved (for manual cleanup/debugging), same as root
lib/artifacts.py's record_account. Self-contained (no root run_context.py machinery) since
e2e stays a separate sibling project. throwaway UAT wallets only -- private key/mnemonic
saved on purpose (same accepted exception as root's own artifacts). best-effort: never fail
a test.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
_file_path: Path | None = None  # resolved lazily, one file per process


def _file() -> Path:
    global _file_path
    if _file_path is None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        _file_path = RESULTS_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.jsonl"
    return _file_path


def record_account(*, role: str, address: str, secret: str) -> None:
    """save a minted wallet. `secret` is the mnemonic (subject) or private key (maker) --
    whichever to_account() needs to re-derive it later."""
    try:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "type": "account",
               "role": role, "address": address, "secret": secret}
        with _file().open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass  # best-effort, drop on any fs error
