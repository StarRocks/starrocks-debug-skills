---
type: reference
category: node
issue: memory-tracker-leak
keywords: [memory tracker, memory leak, INSERT slow, throttling]
---

# Case-007: Memory Tracking Leak Slowing Imports

## Environment

- StarRocks version: 3.3.9
- Architecture: shared-nothing

## Symptom

INSERT tasks continued running but were extremely slow. Progress counters barely moved.

## Investigation

Observed that query memory usage was at capacity. Suspected a known memory tracking leak.

## Root Cause

Known memory tracking leak (PR [#54242](https://github.com/StarRocks/starrocks/pull/54242)) —
the system incorrectly believed memory was exhausted, throttling import throughput.

## Resolution

- Upgrade to the version containing the fix. Memory usage dropped and import sink latency
  returned to normal.

## Impact Chain

This memory tracker leak cascades as follows:

```
Memory tracker reference-count bug accumulates unreleased tracked memory
  → Tracker reports usage near mem_limit even when actual RSS is lower
    → Import admission control activates prematurely
      → Import: new load tasks throttled or rejected (import skill)
        → Load job progress frozen → jobs eventually timeout
    → Memory headroom metrics become misleading
      → Operators may not scale BE nodes despite apparent headroom
    → Actual RSS continues growing undetected
      → Eventually triggers real OOM → BE crash (node/OOM chain)
```

**Cross-skill impact**: import (throttling and timeout), node (eventual OOM if untreated)

**Distinguishing from real OOM**: `curl http://<BE>:<port>/mem_tracker` shows high tracked usage, but `top` shows lower RSS than expected — this gap confirms a tracker leak rather than actual memory exhaustion.

## Lessons Learned

- "Memory at capacity" with healthy queries is a strong signal for a tracker leak rather
  than an actual workload problem.
- Treat the per-module memory tracker (`/mem_tracker`) as the authoritative source when
  diagnosing throttling.

## Related Skills

- [SKILL.md](../SKILL.md) — BE OOM and memory tracking leak section
- [SKILL.md](../SKILL.md) — write-slow diagnosis when memory throttling masquerades as a
  thread-pool issue
