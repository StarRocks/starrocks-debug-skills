---
type: reference
category: node
issue: fe-deadlock
keywords: [FE deadlock, LockManager, ReportHandler, version not found, jstack]
---

# Case-003: FE Deadlock Causing "Version Not Found" Errors (LockManager Deadlock)

## Environment

- StarRocks version: 3.3.11
- Architecture: shared-nothing

## Symptom

Queries reported `version does not exist` — versions had already been recycled on BE.
FE Report timestamps had not updated for a long time.

## Investigation

1. **Suspected FE deadlock**: Report timestamp stagnation is a classic indicator.
2. **Captured jstack**:
   ```bash
   jstack <fe_pid> > /tmp/fe_jstack.log
   ```
3. **Found deadlock**:
   ```
   "ReportHandler" #207 daemon prio=5 ... in Object.wait()
     java.lang.Thread.State: TIMED_WAITING (on object monitor)
       at com.starrocks.common.util.concurrent.lock.LockManager.lock(LockManager.java:105)
       at com.starrocks.common.util.concurrent.lock.Locker.lockDatabase(Locker.java:119)
       at com.starrocks.server.LocalMetastore.getPartitionIdToStorageMediumMap(...)
       at com.starrocks.leader.ReportHandler.tabletReport(...)
   ```

## Root Cause

FE LockManager deadlock — the `ReportHandler` thread couldn't acquire the DB lock,
blocking tablet report processing. BE recycled old versions while FE was unaware.

## Resolution

### Short-term

- Restart FE to recover.

### Long-term

- Preserve jstack dumps and escalate to engineering for code-level fix.

## Lessons Learned

- A stale Report timestamp is the canonical FE-deadlock signal — check it first when
  queries report `version does not exist`.
- Always capture multiple jstack snapshots before restart so engineering can analyze
  the lock graph.

## Impact Chain

This deadlock cascades into multiple failure modes:

```
FE LockManager deadlock (this case)
  → ReportHandler cannot acquire DB lock
    → Tablet version updates frozen (no tablet reports processed)
      → BE recycles old tablet versions normally
        → FE version map becomes stale
          → Query: "version does not exist" errors (query skill)
  → DDL operations (CREATE/DROP/ALTER) hang indefinitely
    → Schema migrations stall
  → Import: Routine Load and Broker Load transactions cannot commit
    → Import jobs report timeout (import skill)
```

**Cross-skill impact**: query (version errors), import (commit timeout), tablet (stale version map)

**Recovery sequence**: Restart FE first to clear deadlock → queries recover automatically → imports require manual retry.

## Related Skills

- [SKILL.md](../SKILL.md) — FE deadlock diagnostic flow
- [SKILL.md](../SKILL.md) — `version does not exist` symptom mapping
