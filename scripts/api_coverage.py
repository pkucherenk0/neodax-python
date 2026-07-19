#!/usr/bin/env python3
"""API endpoint coverage report. port of scripts/api-coverage.mjs.

cross-ref BE endpoint registry (config/api-endpoints.json — "map" of what backends expose)
against endpoints this harness actually calls (scanned from lib/ + suites/ + e2e/ + conftest).

two directions of drift shown:
  1. registry endpoints with NO test reference  -> coverage gaps (what to test next).
  2. paths in tests but NOT in registry -> likely typo or stale registry.

path-level match (method + path, params/query normalized). does not diff request/response
bodies — lib/schemas.py (pydantic) guards that at runtime.

usage: python scripts/api_coverage.py [--strict]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["lib", "suites", "e2e"]
SCAN_FILES = ["conftest.py"]
STRICT = "--strict" in sys.argv


def norm_path(p: str) -> str:
    """normalize path for compare: drop query/hash, collapse f-string `{...}` interp and
    `:param` route params to one `{param}` token, trim trailing slash."""
    s = p.split("?")[0].split("#")[0]
    s = re.sub(r"\{[^}]*\}", "{param}", s)  # f-string interp + {slug} route params
    s = re.sub(r"/:[^/]+", "/{param}", s)  # :symbol (gin style, from the registry)
    s = s.rstrip("/")
    return s or "/"


def key(method: str, path: str) -> str:
    return f"{method.upper()} {norm_path(path)}"


# find `.get('/...')` / `.post(f"/...")` etc call sites, capture method + first string arg.
# handles plain and f-strings. paths passed via variable not captured.
CALL_RE = re.compile(r"\.(get|post|put|delete|patch)\s*\(\s*f?(['\"])(/[^'\"]*)\2", re.IGNORECASE)


def referenced_endpoints() -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    files: list[Path] = []
    for d in SCAN_DIRS:
        files.extend((ROOT / d).rglob("*.py"))
    files.extend(ROOT / f for f in SCAN_FILES)
    for file in files:
        if "template" in file.name.lower():
            continue
        try:
            src = file.read_text()
        except OSError:
            continue
        for m in CALL_RE.finditer(src):
            k = key(m.group(1), m.group(3))
            refs.setdefault(k, set()).add(str(file.relative_to(ROOT)))
    return refs


def main() -> None:
    registry = json.loads((ROOT / "config/api-endpoints.json").read_text())
    endpoints = registry.get("endpoints", [])
    refs = referenced_endpoints()
    ref_keys = set(refs)

    by_service: dict[str, list[dict]] = {}
    for e in endpoints:
        covered = key(e["method"], e["path"]) in ref_keys
        by_service.setdefault(e.get("service", "?"), []).append({**e, "covered": covered})

    registry_keys = {key(e["method"], e["path"]) for e in endpoints}
    orphan_refs = sorted((k, files) for k, files in refs.items() if k not in registry_keys)

    total = covered_total = 0
    print("\nAPI endpoint coverage (registry x tests)\n" + "=" * 46)
    for service in sorted(by_service):
        rows = by_service[service]
        c = sum(1 for e in rows if e["covered"])
        total += len(rows)
        covered_total += c
        print(f"\n{service}  —  {c}/{len(rows)} covered")
        for e in sorted(rows, key=lambda x: x["path"]):
            print(f"  {'✓' if e['covered'] else '·'} {e['method']:<6} {e['path']}")
    pct = round(covered_total / total * 100) if total else 0
    print(f"\n{'=' * 46}\nTOTAL: {covered_total}/{total} endpoints covered ({pct}%)")

    if orphan_refs:
        print("\n⚠️  Referenced in tests but NOT in the registry (typo, or the map is stale — regenerate):")
        for k, files in orphan_refs:
            print(f"  {k}   [{', '.join(sorted(files))}]")

    print()
    if STRICT and orphan_refs:
        print("api-coverage --strict: referenced endpoints missing from the registry.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
