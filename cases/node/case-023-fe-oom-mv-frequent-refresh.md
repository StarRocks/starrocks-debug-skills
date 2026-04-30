---
type: case
category: node
issue: fe-oom-mv-frequent-refresh
keywords: [fe, oom, memory, mv, materialized-view, refresh, slow-lock, lock-manager]
---

# Case-023: FE OOM Due to Frequent MV Refresh

## Environment

- StarRocks version: All versions
- Architecture: shared-nothing / shared-data

## Symptom

**Background:** User has many MV refresh tasks configured with minute or second-level refresh intervals.

**Symptoms observed:**
- FE leader memory continues to rise
- Queries become stuck/slow
- FE log shows slow lock traces

## Investigation

### Step 1: Check FE Memory Trend

```
FE leader memory: Rising trend
Query latency: Increasing/stuck
```

### Step 2: Check FE Logs for Slow Lock

FE log shows slow lock traces:

```
WARN [LockManager.logSlowLockTrace():423] LockManager detects slow lock :
{"owners":[{"id":13479085,"name":"starrocks-taskrun-pool-22075","type":"INTENTION_SHARED","heldFor":6935,...}],
"waiter":[{"id":13446845,"name":"thrift-server-pool-367701","type":"WRITE","waitTime":6895},...]}
```

Slow lock causes:
- Resources cannot be released
- Memory accumulation
- Other operations waiting for locks

### Step 3: Check MV Refresh Configuration

Many MVs configured with minute or second-level refresh intervals.

## Root Cause

**Frequent MV refresh causing lock contention:**

1. **High refresh frequency**: Minute or second-level refresh for many MVs
2. **Lock contention**: MV refresh operations cause frequent lock acquisition
3. **Slow lock**: Lock held for extended time, blocking other operations
4. **Memory accumulation**: Blocked resources cannot be released, memory grows

## Resolution

### Immediate Recovery

Leader GC triggers leader switch, service self-recovers.

### Long-term Solution

**Adjust MV refresh frequency:**

```sql
-- For non-real-time critical scenarios, adjust to hourly or daily refresh
ALTER MATERIALIZED VIEW mv_name 
SET REFRESH EVERY(1 HOUR);

-- Or daily refresh
ALTER MATERIALIZED VIEW mv_name 
SET REFRESH EVERY(1 DAY);
```

**Guidelines:**
- Avoid large number of minute-level MVs triggering simultaneously
- Only use high-frequency refresh for real-time critical scenarios
- Most scenarios should use hourly or daily refresh

## Key Commands

```bash
# Check FE logs for slow lock
grep "LockManager detects slow lock" fe.log

# Check memory usage
curl http://fe_host:8030/metrics | grep -i memory
```

```sql
-- Show MV refresh tasks
SHOW MATERIALIZED VIEWS;

-- Check task run status from information_schema
SELECT 
    TASK_NAME,
    CREATE_TIME,
    FINISH_TIME,
    STATE,
    EXTRA_MESSAGE
FROM information_schema.task_runs
WHERE TASK_NAME LIKE 'mv-%'
ORDER BY CREATE_TIME DESC
LIMIT 10;

-- Alter MV refresh frequency
ALTER MATERIALIZED VIEW mv_name SET REFRESH EVERY(1 HOUR);
```

## Lessons Learned

1. **MV refresh frequency**: Avoid minute/second-level refresh for non-critical scenarios
2. **Lock contention**: High-frequency MV refresh causes lock contention
3. **Slow lock impact**: Slow locks block resource release, causing memory growth
4. **Practical refresh intervals**: Hourly or daily refresh suits most business needs

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [case-012-mv-refresh-failures.md](../materialized-view/case-012-mv-refresh-failures.md) — MV refresh failures