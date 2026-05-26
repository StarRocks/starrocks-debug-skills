---
type: reference
category: concurrency
issue: memory-volatility
keywords: [memory volatility, query_timeout, session override, big query, audit log, governance]
---

# Case-015: Memory Volatility (Session-Level Timeout Override)

## Symptom

Available memory across all BEs dropped sharply, then recovered to near-maximum.
Some BEs restarted (OOM). `query_failed_count` spiked to 10-100x normal levels.

## Investigation

1. Checked BE process start times — confirmed a few BEs had restarted, but most had not.
2. Despite no restart, all BEs showed memory dropping to only a few GB — suspicious.
3. Checked "All Queries" sorted by execution time — found many extremely long-running queries.
4. Verified the documented policy: `query_timeout = 300`. Confirmed via `SHOW VARIABLES`.
5. Searched FE logs for the `query_timeout` keyword — discovered some clients had set
   `query_timeout = 30000` at session level.

## Root Cause

Some clients privately overrode `query_timeout` to 30,000 seconds at session level, allowing
huge queries to run unchecked. These queries:

1. Lacked `WHERE` conditions, returning massive result sets from million- and billion-row tables.
2. Used value columns (not key columns) as join keys on AGGREGATE-model tables.

## Resolution

1. Identified and killed the offending long-running queries.
2. Enforced query timeout governance via resource groups with `big_query_cpu_second_limit`.
3. Added SQL blacklist rules for queries without `WHERE` conditions.

## Impact Chain

This governance gap cascades as follows:

```
No resource group enforcement on session-level timeout overrides
  → Single user sets query_timeout = unlimited
    → Large uncontrolled query runs indefinitely
      → Query: consumes all BE CPU/memory → other queries starve (query skill)
          → P99 latency spikes cluster-wide
      → Import: large query memory pressure reduces import admission headroom (import skill)
          → Concurrent imports throttled
      → Resource isolation: default_wg has no effective ceiling
          → Resource group mem_limit / cpu_core_limit become nominal (resource-isolation skill)
      → Memory pressure rises → risk of BE OOM (node skill)
```

**Cross-skill impact**: query (latency spike), import (throttling), resource-isolation (enforcement gap), node (OOM risk)

**Governance fix**: Enforce resource groups at the classifier level so session variables cannot override group limits; use `ADMIN SET FRONTEND CONFIG` to cap `query_timeout` globally as a backstop.

## Lessons Learned

- Memory volatility with "policy says 5 minutes but queries run for hours" is the
  fingerprint of a session-level override.
- The right response is governance, not policy: enforce limits via resource groups, where
  individual sessions can't bypass them.

## Related Skills

- `skills/09-high-concurrency.md` — memory volatility / hidden session overrides
- `skills/10-resource-isolation.md` — resource-group governance with big-query limits
