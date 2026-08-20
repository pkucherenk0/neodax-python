#!/usr/bin/env python3
"""First-half vs second-half comparison of a k6 raw JSON/NDJSON output file (--out json=...) --
answers "does p95 creep up over time?" / "does the fail rate rise later in the run?", which a
single averaged summary line can't (an average across the whole run hides a trend by design).
Called automatically by ../run.sh after every run -- harmless on a short smoke run, just less
statistically meaningful (flagged below when the sample is small). Pure stdlib, no venv needed.

Usage: python3 perf/tools/time_trend_report.py <ndjson-file>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

MIN_MEANINGFUL_SAMPLE = 20  # below this, a half-vs-half split is mostly noise


def p95(values: list) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    idx = min(int(len(s) * 0.95), len(s) - 1)
    return s[idx]


def stats(bucket, label: str) -> None:
    values = [v for _, v, _ in bucket]
    fails = sum(1 for _, _, st in bucket if st != "200")
    total = len(bucket)
    fail_rate = (fails / total * 100) if total else 0
    avg = sum(values) / total if total else 0
    print(f"{label:>12} | requests={total:>6} | p95={p95(values):>8.1f}ms | "
          f"avg={avg:>7.1f}ms | fail_rate={fail_rate:>5.2f}% ({fails} failed)")


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <ndjson-file>", file=sys.stderr)
        raise SystemExit(1)
    path = sys.argv[1]

    durations = []
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "Point" or d.get("metric") != "http_req_duration":
                continue
            t = datetime.fromisoformat(d["data"]["time"])
            durations.append((t, d["data"]["value"], d["data"]["tags"].get("status")))

    print()
    print("=== Time-trend: first half vs second half of the run ===")
    if not durations:
        print("No http_req_duration data points found -- did the run actually make requests?")
        return
    if len(durations) < MIN_MEANINGFUL_SAMPLE:
        print(f"Only {len(durations)} requests -- too few for a meaningful trend (smoke-scale run).")
        print("Showing the split for reference anyway:")

    durations.sort(key=lambda x: x[0])
    start, end = durations[0][0], durations[-1][0]
    midpoint = start + (end - start) / 2
    first_half = [x for x in durations if x[0] < midpoint]
    second_half = [x for x in durations if x[0] >= midpoint]

    print(f"Run window: {start} -> {end}  (midpoint: {midpoint})")
    print()
    stats(first_half, "FIRST HALF")
    stats(second_half, "SECOND HALF")
    print()

    p95_first, p95_second = p95([v for _, v, _ in first_half]), p95([v for _, v, _ in second_half])
    fail_first = (sum(1 for _, _, st in first_half if st != "200") / len(first_half) * 100) if first_half else 0
    fail_second = (sum(1 for _, _, st in second_half if st != "200") / len(second_half) * 100) if second_half else 0

    print("=== Answers ===")
    p95_delta = p95_second - p95_first
    print(f"p95 latency creeping up? {'YES' if p95_delta > p95_first * 0.15 else 'no significant change'} "
          f"({p95_first:.1f}ms -> {p95_second:.1f}ms, delta {p95_delta:+.1f}ms)")
    fail_delta = fail_second - fail_first
    print(f"Fail rate rising in back half? {'YES' if fail_delta > 1.0 else 'no significant change'} "
          f"({fail_first:.2f}% -> {fail_second:.2f}%, delta {fail_delta:+.2f} points)")
    print()
    print("Note: this is the CLIENT (k6) view only. To see whether a server-side resource")
    print("(CPU/RAM/DB connections) correlates with any trend above, watch it directly during")
    print("the run -- see FAILURE_MODES.md in /Users/pk/Documents/LearningQA/performance for what to look for.")


if __name__ == "__main__":
    main()
