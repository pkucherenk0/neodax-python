"""loads .arrangement.json, written by tools/arrange_metamask_e2e.py --out before every run.
maker access_token ttl 60s -- never reuse across sessions. mirrors old wallet.ts Arrangement type.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ARRANGEMENT_PATH = Path(__file__).resolve().parent.parent / ".arrangement.json"


@dataclass(frozen=True)
class Subject:
    address: str
    mnemonic: str


@dataclass(frozen=True)
class Maker:
    address: str
    access_token: str


@dataclass(frozen=True)
class Env:
    trading_base: str
    auth_base: str


@dataclass(frozen=True)
class Arrangement:
    env: Env
    market: str
    subject: Subject
    maker: Maker


def load_arrangement(path: Path = ARRANGEMENT_PATH) -> Arrangement:
    if not path.exists():
        raise RuntimeError(
            f"{path} not found -- run tools/arrange_metamask_e2e.py --out {path} first "
            "(from the repo root, root .venv active)."
        )
    data = json.loads(path.read_text())
    return Arrangement(
        env=Env(**data["env"]),
        market=data["market"],
        subject=Subject(**data["subject"]),
        maker=Maker(**data["maker"]),
    )
