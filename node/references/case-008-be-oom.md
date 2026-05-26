---
type: reference
category: node
issue: be-oom
keywords: [BE OOM, publish timeout, pstack, cluster recovery]
---

# Case-008: Publish Timeout Cascade Caused by BE OOM

## Environment

- StarRocks version: 3.3.16
- Architecture: shared-nothing

## Symptom

Cluster experienced widespread publish timeouts.

## Investigation

1. Ran `pstack` on some nodes for diagnostics.
2. The nodes that did **not** have pstack running experienced OOM.
3. Approximately 30 minutes after the OOM events, all committed tasks were automatically
   consumed and the cluster recovered.

## Root Cause

BE node OOM caused publish processing to completely stall, blocking all publish operations
for affected tables.

## Resolution

- Restart OOM'd nodes to recover.
- Investigate the root cause of OOM (large queries or memory leaks) and address upstream.

## Impact Chain

This BE OOM cascades as follows:

```
Large query exceeds BE mem_limit (this case)
  → Linux OOM Killer kills BE process
    → In-flight tablet write transactions lose their writer context
      → Import: all load jobs targeting tablets on that BE report "publish timeout" (import skill)
    → FE marks BE as dead; triggers replica recovery
      → Balance: clone tasks flood the cluster to restore replica count (balance skill)
        → Clone IO competes with compaction → compaction lag rises (compaction skill)
    → After BE restarts and rejoins, publish queue drains
      → ~30-minute recovery window with degraded cluster capacity
```

**Cross-skill impact**: import (publish timeout), balance (clone flood), compaction (lag from clone IO)

**Leading indicator**: `grep "large memory alloc" be.WARNING` before OOM — allows pre-emptive query kill before OOM Killer fires.

## Lessons Learned

- Cluster-wide publish timeouts often originate from a small number of OOM'd BEs — start
  by listing process-start times for all BEs.
- Recovery is typically automatic once OOM'd nodes restart, but the underlying memory
  pressure must still be diagnosed.

## Related Skills

- [SKILL.md](../SKILL.md) — BE OOM and crash investigation
- [SKILL.md](../SKILL.md) — publish timeout diagnosis
