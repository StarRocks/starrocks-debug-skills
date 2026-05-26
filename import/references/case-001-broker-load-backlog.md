---
type: reference
category: import
issue: broker-load-backlog
keywords: [broker load, async_load_task_pool_size, ReportExecStatus, txn manager timeout]
---

# Case-001: Broker Load Task Backlog (Task Queue Saturation)

## Environment

- StarRocks version: 2.2.15
- Architecture: shared-nothing

## Symptom

Broker Load tasks backed up massively. Newly submitted tasks waited a long time before
execution.

## Investigation

1. **Initial assessment**: loading thread pool was full. Increased `async_load_task_pool_size = 30`
   as a temporary workaround.
2. **Second escalation**: too many loading tasks saturated cluster IO. Reduced to
   `async_load_task_pool_size = 15`.
3. **FE log deep-dive**:
   - Tasks suddenly started experiencing long waits at a specific timestamp.
   - Traced the first blocked task ID backward to find a prior task cancelled due to
     `timeout by txn manager`.
   - That task had been running for 4 hours, exceeding the transaction timeout.
4. **BE log trace**:
   - Followed the chain: task ID -> query ID -> instance ID -> execution thread ID.
   - Found `ReportExecStatus() to TNetworkAddress(...) failed:` — BE failed to report load
     status back to FE.

## Root Cause

After the BE instance finished execution, the status report back to FE failed. FE never
learned the execution result, causing the task state to be lost and the thread pool slot
to remain occupied.

## Resolution

### Short-term

- Cancel stuck tasks; restart affected BE if necessary.
- Tune `async_load_task_pool_size` based on observed cluster IO headroom.

### Long-term

- Improve FE-BE status reporting reliability so failed reports do not leak pool slots.

## Key Commands

```bash
# Trace by task ID in FE logs
grep "<task_id>" fe.log

# Trace by instance ID + thread ID in BE logs
grep "<instance_id>" be.INFO
grep "thread_id=<id>" be.INFO
```

## Lessons Learned

- Pool exhaustion is often a symptom, not a cause. Always trace the *first* stuck task to
  understand the original failure.
- The `label -> txn_id -> query_id -> instance_id -> thread_id` chain is the canonical
  trace path for any import incident.

## Related Skills

- [SKILL.md](../SKILL.md) — Broker Load backlog and write-slow diagnosis
