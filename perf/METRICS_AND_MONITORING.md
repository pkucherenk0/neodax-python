# Metrics & monitoring — what to watch, client-side and server-side

Companion to `README.md` (that one says *how* to run things, this says *what to look at*). Two
halves: what k6 itself gives you (client's view), and what to watch server-side (Grafana or
otherwise) — this repo doesn't operate UAT's infrastructure, so how much of the second half
applies depends on what access you actually have.

## 1. What k6 reports — every run, client-side only

k6 only ever sees what a client sees: did the request succeed, how long did it take. It cannot
tell you *why* — that's the whole reason for §2 below.

| Metric | What it means | Where you see it |
|---|---|---|
| `http_req_duration` | Per-request latency: avg/min/med/max/p90/**p95**/**p99** | Terminal summary, every run |
| `http_req_failed` | Fraction of requests that errored (non-2xx or network failure) | Terminal summary + `thresholds` gate |
| `checks` | Pass rate of your named `check()` assertions, broken out **per check name** | Terminal summary — the most specific signal you get: tells you *which* endpoint failed, not just "something did" |
| `http_reqs` | Total request count + rate (req/s) — your actual achieved throughput | Terminal summary |
| `iterations` / `vus` / `vus_max` | How many times the script ran, how much concurrency was active | Terminal summary |
| `data_sent` / `data_received` | Bytes over the wire | Terminal summary |
| **Full status-code histogram** | Every distinct code seen (not just 200-vs-not) | `tools/status_code_breakdown.sh`, run automatically by `run.sh` — needed because k6's own summary only tracks tag values a threshold references, so a stray 400/429/503 is otherwise invisible |
| **First-half vs second-half trend** | Is p95/fail-rate creeping up over the run's own duration | `tools/time_trend_report.py`, run automatically by `run.sh` |

**p95/p99, not average.** An average hides a bad tail — 99 fast requests and 1 that took 5
seconds average out to "fine." Percentiles don't. Every threshold in this suite is written
against p95 (and p99 where it matters) for that reason.

**`checks` vs `thresholds`.** A `check()` is a per-request assertion that never fails the run by
itself — it just gets counted. A `thresholds` entry is the aggregate pass/fail gate: cross it
and k6 exits non-zero. That exit code is the actual "alert" here — `echo $?` after any
`perf/run.sh` invocation tells you pass/fail without reading a line of output, and is what you'd
wire into CI if this suite's stance on that ever changes.

## 2. What to watch server-side, if you have any visibility

k6's client view says latency went up; it can't say *why* — that needs a metric from the system
doing the work.

| Access | Do this |
|---|---|
| Existing Grafana/Prometheus for the backend (`portfolio_manager_perp`, liquidation, auth, faucet) | overlay the k6 run's time window on those dashboards, watch the same minute both sides — the only way to tell "DB bottleneck" from "connection pool too small" from "GC pauses" apart, since all three look identical from k6's side |
| No backend access | limited to §1's client-side view + the local k6→Prometheus dashboard (`README.md`'s "Visualizing results") — good for spotting *that* and *when* something degraded, not *why*. Flag as a finding for whoever does have access (same gap `FAILURE_MODES.md` frames for its own practice targets) |

### What's actually worth looking for, by resource

Backend is Go (`converters.go`, `lock_oneway.go`/`lock_hedge.go`), so "language-specific
tooling" specifically means:

| Resource | Symptom server-side | Symptom in k6 | Go-specific tool |
|---|---|---|---|
| **Memory / leaks** | RSS climbs and never plateaus under *constant* load (plateauing = normal cache/pool warmup, not a leak) | Latency creeps up over a run's duration, then errors near OOM | `net/http/pprof` heap profile; `docker stats`/`kubectl top` for RSS |
| **Goroutine leaks** | Goroutine count climbs alongside RSS (each holds buffers) | Sudden failure cliff once the scheduler/GC can't keep up, not a gradual creep | `pprof` goroutine profile |
| **GC pressure** | CPU rises *with* RSS; GC pause time (p99) climbs | Latency **p99 spikes intermittently** — a stutter pattern, not steady degradation | `pprof` — GC trace |
| **CPU** | Scales worse than linearly with load (e.g. lock contention: CPU looks idle-ish, but everything's waiting) | Latency balloons well before CPU% looks "full" | `pprof` CPU profile |
| **Connection/DB pool exhaustion** | App threads block waiting for a connection; DB itself may be near-idle — misleading if you only watch the DB | Latency spikes then timeouts, with no CPU/RAM correlation at all | N/A — this is a config/sizing issue, not a code profile |
| **GPU** | — | — | **Not applicable to this stack.** No GPU-bound component here (REST/matching-engine services, no ML inference) — don't spend time instrumenting for it. |

**Leak vs. legitimate warm-up**, in one line: a real leak never flattens under *constant* load.
If a metric climbs then plateaus once load itself plateaus, that's normal warm-up, not a leak —
telling the two apart needs a run *long enough to see whether the line ever bends flat*, which
is exactly what a soak test is for (see the gap noted below).

For the full generic version of this table (cascading failures, thundering herd, retry storms,
timeout mismatches, a full plain-language glossary of every abbreviation above) see
`/Users/pk/Documents/LearningQA/performance/FAILURE_MODES.md` — not duplicated here since it
isn't NeoDax-specific.

## 3. Which tier surfaces which failure mode

| Tier / script | Shape | Good at exposing |
|---|---|---|
| `market-data/smoke.js` | 1 VU, brief | Broken deploy, obviously wrong endpoint — not a resource test at all |
| `market-data/load.js` | Ramp to steady peak | Whether normal read traffic causes gradual resource creep |
| `market-data/stress.js` | Escalating steps past peak | Where market-data reads actually start to degrade, and whether they recover once load drops (its final stage) |
| `account-reads/smoke.js` | 1 VU, brief | Auth/token-refresh plumbing works at all |
| `account-reads/load.js` | Ramp, capped at account count | Whether the auth/refresh path holds up under realistic concurrent read traffic |
| `order-placement/smoke.js` | 1 VU, 3 iterations | Order-placement + cancel latency and correctness at minimal load |
| `order-placement/load.js` | Ramp, capped at account count | Whether order-placement/cancel latency holds up under realistic concurrent trading traffic — the highest-value question this whole suite answers, since order-placement degrading under load is the most operationally important failure mode for a trading system |
| `order-placement/stress.js` | Escalating steps past peak, capped at account count | Where order placement actually starts to degrade, and whether it recovers once load drops (its final stage). Stays safe to escalate because every order still never fills (no position/PnL risk) and cancellation retries at every scale — see `lib/orders.js` |
| `order-matching/smoke.js` | 1 VU, 3 iterations, maker+taker pair | Whether the match path itself (rest → cross → fill → confirm → flatten) works correctly at minimal load — a qualitatively different question from Tier 3, which never exercises matching at all. **Smoke-only for now**, by explicit choice: this is new coordination logic (maker/taker pairing, fill-confirm polling, dual-sided flatten) that needs to be proven correct at 1 VU before any load/stress shape gets built on top of it. |

**Gap, on purpose (for now):** no `soak.js` exists in any tier yet, so nothing here can currently
answer the leak question in §2 — a leak needs sustained load over a long duration to distinguish
from normal warm-up, which none of the current scripts are shaped for. Likewise, `order-matching/`
has no `load.js`/`stress.js` yet either — real fills are a fundamentally higher-risk category than
Tier 3's never-filling orders, so that escalation (if it happens) needs its own deliberate
go-ahead, not an automatic "make it bigger" once smoke passes. If you add a soak test, model it on
the practice repo's `test-types/soak-test.js` (moderate, sustained VUs, long duration) against
Tier 1 first — it's the only tier with no account-count ceiling to worry about over a multi-hour
run.
