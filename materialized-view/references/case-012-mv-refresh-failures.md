---
type: reference
category: materialized-view
issue: refresh-failures
keywords: [MV refresh, FE abort, S3 rate limit, current_date, FE restart, scheduler persistence]
---

# Case-012: Materialized View Refresh Failures (Multi-Cause)

## Environment

- StarRocks version: 3.3.15
- Architecture: shared-data on AWS S3

## Symptom

Materialized view auto-refresh failed repeatedly across multiple MVs.

## Investigation

1. **Initial refresh failure**: MV's first auto-refresh failed mid-process with FE aborting the task.
2. **Subsequent refresh failure**: next scheduled refresh failed with S3 rate-limiting error
   `503: Please reduce your request rate`.
3. **Access issue**: `SHOW TABLETS` failed with `IllegalStateException` due to ephemeral role
   context missing.
4. **Non-deterministic function**: MV contained `current_date()` causing invalid rewrite plan.
5. **FE restart impact**: daily FE restart broke MV schedule persistence — scheduler re-registered
   tasks with wrong `initialDelay`, skipping the next scheduled run.

## Diagnostic Commands Used

```sql
-- Check MV health
SHOW MATERIALIZED VIEWS LIKE 'mv_name'\G;

-- Check task queue congestion
SELECT state, COUNT(1) FROM information_schema.task_runs GROUP BY state;

-- Check concurrency setting
ADMIN SHOW FRONTEND CONFIG LIKE 'task_runs_concurrency';

-- Adjust schedule temporarily
ALTER MATERIALIZED VIEW mv_name REFRESH ASYNC EVERY (INTERVAL 12 HOUR);
```

## Lessons Learned

- Avoid `current_date()` or other non-deterministic functions in MV definitions.
- Ensure FE uptime covers at least one full scheduling cycle.
- Increase `task_runs_concurrency` when async task queue grows large.
- Need persistent MV scheduler metadata to survive FE restarts.
- S3 rate limit hits on refresh argue for partitioned-prefix storage volumes (see [SKILL.md](../SKILL.md)).

## Related Skills

- `skills/04-materialized-view.md` — MV refresh diagnostics and operational pitfalls
- [SKILL.md](../SKILL.md) — S3 rate-limit mitigation
