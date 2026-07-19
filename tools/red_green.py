#!/usr/bin/env python3
"""red-green: prove a test can FAIL (anti-false-positive, CONVENTIONS §13).
port of scripts/red-green.mjs.

runs the target test once (must PASS = baseline green), then MUTATES its expected values
(flip booleans, bump numbers, corrupt string literals on the right side of comparisons inside
assert lines / pytest.approx calls) and re-runs: a real test now goes RED. if it still
PASSES, the assertions are vacuous -> reported. file restored always.

usage: python tools/red_green.py <test_file> [-k "name"] [--env=uat] [--force]
SAFETY: trades/serial place LIVE orders even with mutated expectations -> refused unless --force.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def parse_args(argv: list[str]) -> tuple[str, str | None, str, bool]:
    file = next((a for a in argv if not a.startswith("-")), None)
    if not file:
        print("usage: python tools/red_green.py <test_file> [-k 'name'] [--env=uat] [--force]", file=sys.stderr)
        sys.exit(2)
    grep = argv[argv.index("-k") + 1] if "-k" in argv else None
    env = next((a.split("=", 1)[1] for a in argv if a.startswith("--env=")), "uat")
    return file, grep, env, "--force" in argv


# mutate literal expected values on the RIGHT of comparisons in assert lines:
#   == True/False/number/'str'   >= number   <= number   > number   < number
# and inside pytest.approx(<number>. leaves dynamic/expression args untouched.
CMP = re.compile(r"(==|>=|<=|>|<)\s*(True|False|-?\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\")")
APPROX = re.compile(r"(pytest\.approx\(\s*)(-?\d+(?:\.\d+)?)")


def mutate_expected(code: str) -> tuple[str, int]:
    count = 0

    def repl_cmp(m: re.Match) -> str:
        nonlocal count
        op, lit = m.group(1), m.group(2)
        if lit == "True":
            mutated = "False"
        elif lit == "False":
            mutated = "True"
        elif lit[0] in "'\"":
            mutated = f"{lit[0]}__RG_MUTATED__{lit[0]}"
        else:
            mutated = str(float(lit) + 777) if "." in lit else str(int(lit) + 777)
        count += 1
        return f"{op} {mutated}"

    def repl_approx(m: re.Match) -> str:
        nonlocal count
        count += 1
        lit = m.group(2)
        mutated = str(float(lit) + 777) if "." in lit else str(int(lit) + 777)
        return f"{m.group(1)}{mutated}"

    out_lines = []
    for line in code.split("\n"):
        # only touch assert lines so we don't corrupt arrange/act values.
        if line.lstrip().startswith("assert "):
            line = CMP.sub(repl_cmp, line)
            line = APPROX.sub(repl_approx, line)
        out_lines.append(line)
    return "\n".join(out_lines), count


def run(file: str, grep: str | None, env: str) -> bool:
    cmd = [sys.executable, "-m", "pytest", file, f"--env={env}", "-m", ""]  # -m "" lifts default lane filter
    if grep:
        cmd += ["-k", grep]
    return subprocess.run(cmd).returncode == 0  # True = passed


def main() -> None:
    file, grep, env, force = parse_args(sys.argv[1:])
    src = Path(file).read_text()
    if re.search(r"pytest\.mark\.(trades|serial)", src) and not force:
        print(f"REFUSED: {file} looks trades/serial — mutated expectations still place LIVE orders.\n"
              f"Run red-green on stateless tests, or pass --force if you accept live execution.", file=sys.stderr)
        sys.exit(2)

    mutated, count = mutate_expected(src)
    if count == 0:
        print("No mutatable literal expectations found (all expected values are dynamic). Red-green manually.",
              file=sys.stderr)
        sys.exit(2)

    print("\n[red-green] baseline run (expect PASS)...")
    if not run(file, grep, env):
        print("\n[red-green] baseline FAILED — fix the test first (it must pass before we prove it can fail).",
              file=sys.stderr)
        sys.exit(1)

    print(f"\n[red-green] mutating {count} expected value(s) and re-running (expect FAIL)...")
    try:
        Path(file).write_text(mutated)
        mutated_pass = run(file, grep, env)
    finally:
        Path(file).write_text(src)  # always restore

    if mutated_pass:
        print("\n❌ [red-green] test STILL PASSED with corrupted expected values -> VACUOUS / "
              "false-positive. Fix the assertions.", file=sys.stderr)
        sys.exit(1)
    print("\n✅ [red-green] test went RED when expectations were corrupted -> it genuinely asserts. Good.")


if __name__ == "__main__":
    main()
