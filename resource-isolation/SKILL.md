---
name: 10-resource-isolation
description: "Use when setting up or troubleshooting resource groups, query queues, or big-query circuit breakers — or when a runaway query is starving other workloads. Covers resource group classifier mismatches (queries falling to default_wg), CPU/memory/scan limit calibration from audit log percentiles, SQL blacklist patterns, query queue full rejection errors, and emergency statistics collection disable when BRPC is saturated."
version: 2.0.0
category: resource-isolation
keywords:
- resource group
- query queue
- SQL blacklist
- big query
- circuit breaker
- governance
tools: []
related_cases:
- case-002-rpc-failed-statistics
- case-015-memory-volatility
---

# 10 - Resource Isolation, Query Queue, and Query Governance

Investigation guide for resource groups, query queues, big-query circuit breakers, and SQL
blacklisting.

Five root causes account for most resource isolation issues:

- **Cause A** — Unclassified queries monopolize resources (fall into `default_wg`)
- **Cause B** — Memory limit too tight causing disk spill and IO contention
- **Cause C** — Queue full causing immediate rejection (misleading error to client)
- **Cause D** — Big query unbounded for a specific user (no `big_query_*` limits set)
- **Cause E** — Statistics collection saturating BRPC worker threads

---

## Metric Taxonomy — Read This First

Before diagnosing, understand the observable signals for resource isolation issues.

### FE metrics

| Metric | Meaning |
|---|---|
| `starrocks_fe_query_resource_group` | Query count per resource group — which groups are being used |
| `starrocks_fe_query_resource_group_latency` | Percentile latencies per group — which groups have high P99 |
| `starrocks_fe_query_resource_group_err` | Error count per group — which groups are rejecting queries |

**How to retrieve**:

```bash
# All resource group metrics from FE
curl -s "http://<fe_host>:8030/metrics?type=json" \
  | python3 -m json.tool \
  | grep -A 5 "resource_group"

# Or raw metrics
curl -s "http://<fe_host>:8030/metrics" | grep resource_group
```

### BE metrics

| Metric | Meaning |
|---|---|
| `starrocks_be_resource_group_cpu_limit_ratio` | CPU allocation ratio per group per BE node |
| `starrocks_be_resource_group_mem_limit_bytes` | Memory limit in bytes per group per BE node |

**How to retrieve**:

```bash
# BE resource group metrics
curl -s "http://<be_host>:8040/metrics" | grep resource_group

# Loop across all BEs
for be in <be1> <be2> <be3>; do
    echo "=== $be ==="
    curl -s "http://$be:8040/metrics" | grep resource_group
done
```

### Query queue metrics

| Metric / Observable | Meaning |
|---|---|
| `query_queue_mem_used_pct_limit` | Memory usage threshold that triggers queuing |
| Queue depth in FE metrics | How many queries are currently waiting |
| FE log `query rejected: queue is full` | Queue at capacity — new queries rejected immediately |

**How to retrieve**:

```bash
# Check queue configuration
mysql -e "ADMIN SHOW FRONTEND CONFIG LIKE 'query_queue%';"

# Check queue depth in FE metrics
curl -s "http://<fe_host>:8030/metrics" | grep query_queue

# Check if queries are being queued or rejected
grep -E "queue is full|admitted to queue|query rejected" fe/log/fe.log | tail -20
```

### Audit log signals

| Field | Meaning |
|---|---|
| `resource_group` | Which resource group the query was classified into |
| `cpuCostNs` | Total CPU cost in nanoseconds — use for `cpu_core_limit` tuning |
| `memCostBytes` | Peak memory — use for `big_query_mem_limit` tuning |
| `scanRows` | Rows scanned — use for `big_query_scan_rows_limit` tuning |

**How to retrieve**:

```bash
# Queries assigned to default_wg (unclassified)
grep "resource_group=default_wg" fe/log/fe.audit.log | wc -l

# CPU cost per user (for cpu_core_limit tuning)
awk -F'|' '{
    for(i=1;i<=NF;i++) {
        if($i ~ /^User=/) user=substr($i,6)
        if($i ~ /^cpuCostNs=/) cpu=substr($i,12)+0
    }
    print user, cpu
}' fe/log/fe.audit.log | awk '{a[$1]+=$2} END{for(u in a) print a[u]/1e9, u}' | sort -rn | head -10

# Memory outliers
grep "memCostBytes" fe/log/fe.audit.log \
  | awk -F'memCostBytes=' '{print $2}' | cut -d'|' -f1 | sort -rn | head -10
```

---

**Key rules for interpretation**:

1. CPU limit in resource groups is **soft** (proportional sharing when contended); memory limit is **hard** (query fails immediately when exceeded). Tune memory limits conservatively to avoid false kills.
2. Queries classified into `default_wg` have no effective CPU or memory limits — `default_wg` cannot be altered. If queries fall here, add a classifier.
3. `starrocks_fe_query_resource_group_err` counts both hard memory kills and queue rejections — drill into FE log to distinguish.
4. Resource group settings are per-BE — `mem_limit = 30%` means 30% of each individual BE's memory, not 30% of the cluster total.

---

## Resource Isolation Issue Reference

| Symptom | Root cause | Quick confirm |
|---|---|---|
| Queries fall into `default_wg`; no resource classification | No classifier matches the query | `grep "resource_group=default_wg" fe.audit.log \| wc -l` |
| `Memory limit exceeded` error but CPU is fine | `mem_limit` too tight for operator peak | Check `memCostBytes` P99 in audit vs `mem_limit` setting |
| Queries rejected immediately (not after timeout) | Queue full — `max_queued_queries` reached | `grep "queue is full" fe.log \| tail -5` |
| Big query runs for hours; no circuit breaker fires | No `big_query_*` limits on resource group | `SHOW RESOURCE GROUPS` — check big_query_* fields |
| All queries slow; BRPC thread pool saturated | Statistics collection flooding BRPC | `grep "rpc.*stat" fe.log` — check case-002 pattern |
| Small queries starve during large analytical query | Unclassified large query in `default_wg` | `SHOW PROC '/current_queries'` — look for no resource_group |
| Spilling query causes collateral IO slowdown | `mem_limit` too low; disk spill on shared drive | BE disk await time high; spill dir same as data dir |

---

## Phase 1 — Identify the Issue

### Step 1.1 — Check query classification

```sql
-- Are queries being classified into resource groups?
-- Check a running query's resource group:
EXPLAIN VERBOSE <SQL>;
-- Look for: ResourceGroup: <group_name>

-- Set resource group for current session (for testing):
SET resource_group = '<group_name>';
```

```bash
# What fraction of queries go to default_wg (unclassified)?
grep "resource_group=default_wg" fe/log/fe.audit.log | wc -l
grep "resource_group=" fe/log/fe.audit.log | wc -l
# High ratio → Cause A
```

### Step 1.2 — Check for memory limit kills and queue rejections

```bash
# Memory limit exceeded errors
grep -E "Memory limit exceeded|MemTracker exceed" be/log/be.log | tail -10
# → Cause B

# Queue full rejections
grep -E "queue is full|query rejected" fe/log/fe.log | tail -10
# → Cause C

# Unbounded big queries (no circuit breaker fired)
grep "$(date +'%Y-%m-%d')" fe/log/fe.audit.log \
  | awk -F'QueryTime=' 'NF>1{qt=$2+0; if(qt>600000) print}' | tail -10
# QueryTime > 600s → Cause D
```

### Step 1.3 — Check statistics collection saturation

```bash
# Statistics-related BRPC errors
grep -E "rpc.*statistic|statistic.*rpc|brpc.*fail" fe/log/fe.log | tail -10
# → Cause E
```

**Issue signal → Cause mapping**:

| Pattern | Points toward |
|---|---|
| High `default_wg` query ratio | **Cause A** — unclassified queries |
| `Memory limit exceeded` on specific resource group | **Cause B** — mem_limit too tight |
| `queue is full` in FE log; immediate client rejection | **Cause C** — queue full |
| Queries from one user running for hours; no kill | **Cause D** — unbounded big query |
| BRPC failures correlated with statistics collection schedule | **Cause E** — stats collection saturation |

---

## Phase 2 — Measure Current State

### Step 2.1 — List all resource groups and their configuration

```sql
SHOW RESOURCE GROUPS ALL;
-- Key fields: cpu_core_limit, mem_limit, concurrency_limit
-- big_query_cpu_second_limit, big_query_scan_rows_limit, big_query_mem_limit
```

### Step 2.2 — Measure actual resource usage from audit log

```sql
-- CPU usage per user (last 30 days) — for cpu_core_limit tuning
SELECT user, SUM(cpuCostNs)/1e9 AS cpu_sec
FROM starrocks_audit_db__.starrocks_audit_tbl__
WHERE state IN ('EOF','OK')
  AND timestamp >= now() - INTERVAL 30 DAY
GROUP BY user
ORDER BY cpu_sec DESC;

-- Memory P99 per user — for big_query_mem_limit tuning
SELECT user, percentile_approx(memCostBytes, 0.99) AS mem_p99
FROM starrocks_audit_db__.starrocks_audit_tbl__
WHERE state IN ('EOF','OK')
GROUP BY user
ORDER BY mem_p99 DESC;

-- Query time P99 per user — for big_query_cpu_second_limit tuning
SELECT user, percentile_approx(queryTime, 0.99)/1000 AS time_p99_sec
FROM starrocks_audit_db__.starrocks_audit_tbl__
WHERE state IN ('EOF','OK')
GROUP BY user
ORDER BY time_p99_sec DESC;
```

---

## Phase 3 — Take Action by Cause

### Cause A — Unclassified Queries Monopolize Resources

**Confirm all match**:
- High fraction of queries in `fe.audit.log` show `resource_group=default_wg`
- `SHOW PROC '/current_queries'` shows queries with no resource group or `default_wg`
- Small queries starve when large queries run

**Mechanism**: Queries that match no classifier are assigned to `default_wg`, which has no configurable CPU or memory limits. A single large analytical query in `default_wg` consumes all available BE CPU and memory, starving smaller concurrent queries. `default_wg` cannot be modified — the fix is to create classifiers that capture all query patterns.

**Actions**:

```sql
-- Step A-1: Create resource groups for each user type
CREATE RESOURCE GROUP rg_bi
TO (user='bi_user', role='bi_role')
PROPERTIES (
    "cpu_core_limit" = "10",
    "mem_limit" = "30%",
    "concurrency_limit" = "10",
    "big_query_cpu_second_limit" = "600",
    "big_query_scan_rows_limit" = "1000000000",
    "big_query_mem_limit" = "10737418240"
);

CREATE RESOURCE GROUP rg_etl
TO (user='etl_user')
PROPERTIES (
    "cpu_core_limit" = "16",
    "mem_limit" = "40%",
    "concurrency_limit" = "5"
);

-- Step A-2: Create a catch-all group to prevent default_wg fallback
CREATE RESOURCE GROUP rg_default_catch
TO ()  -- matches all unmatched queries
PROPERTIES (
    "cpu_core_limit" = "4",
    "mem_limit" = "20%",
    "concurrency_limit" = "20"
);

-- Step A-3: Verify classification is working
-- Run a query and check EXPLAIN VERBOSE for ResourceGroup field
EXPLAIN VERBOSE SELECT COUNT(*) FROM <table>;
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause B — Memory Limit Too Tight Causing Disk Spill

**Confirm all match**:
- BE log shows `query spill to disk` or `MemTracker exceed limit`
- Disk await time high on BE hosts during affected queries
- Spill directory is on the same disk as data storage

**Mechanism**: Resource group memory limits are hard limits — when a query's operator (sort buffer, hash table) exceeds the group's `mem_limit`, it spills intermediate data to disk. If the spill directory shares a disk with tablet data storage, compaction, and scan operations, IO contention degrades all queries on that BE. Multiple concurrent spilling queries compound the effect.

**Actions**:

```bash
# Step B-1: Identify current mem_limit vs actual P99 usage
# (Use audit log query from Phase 2 Step 2.2)

# Step B-2: Configure dedicated spill directory on separate disk
# In be.conf:
# spill_local_storage_dir = /data/spill/starrocks
mkdir -p /data/spill/starrocks
echo "spill_local_storage_dir = /data/spill/starrocks" >> be/conf/be.conf
# Requires BE restart
```

```sql
-- Step B-3: Increase mem_limit based on P99 usage (set to 1.5x P99)
ALTER RESOURCE GROUP rg_bi SET PROPERTIES (
    "mem_limit" = "45%"
);

-- Step B-4: Set big_query_mem_limit as circuit breaker (2x P99)
ALTER RESOURCE GROUP rg_bi SET PROPERTIES (
    "big_query_mem_limit" = "21474836480"  -- 20GB
);
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause C — Queue Full Causing Immediate Rejection

**Confirm all match**:
- FE log shows `query rejected: queue is full`
- Client receives immediate connection error (not after a wait timeout)
- `query_queue_max_queued_queries` is reached during burst traffic

**Mechanism**: When a resource group reaches `concurrency_limit`, subsequent queries enter the queue. When `query_queue_max_queued_queries` is reached, additional queries are rejected immediately with an error — not after a timeout. Clients without retry logic interpret this as a cluster crash rather than resource exhaustion. The error message may appear as a generic connection error at the application layer.

**Actions**:

```bash
# Step C-1: Confirm queue full is the rejection cause
grep "queue is full" fe/log/fe.log | tail -10

# Step C-2: Check current queue configuration
mysql -e "ADMIN SHOW FRONTEND CONFIG LIKE 'query_queue%';"
```

```sql
-- Step C-3: Increase queue capacity to absorb bursts
ADMIN SET FRONTEND CONFIG ("query_queue_max_queued_queries" = "2000");

-- Step C-4: Increase concurrency limit based on actual peak concurrency
-- (Use audit log P99 concurrency per user from Phase 2)
ALTER RESOURCE GROUP rg_bi SET PROPERTIES (
    "concurrency_limit" = "20"  -- 1.5x of observed P99 peak
);

-- Step C-5: Disable queue for privileged users (e.g., ETL jobs)
ALTER USER 'etl_user' SET PROPERTIES ("session.enable_query_queue" = "false");

-- Step C-6: Set queue wait timeout so clients get a clear timeout error
ADMIN SET FRONTEND CONFIG ("query_queue_pending_timeout_second" = "300");
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause D — Big Query Unbounded for a User

**Confirm all match**:
- Audit log shows queries from specific user with `QueryTime` >5 minutes
- `SHOW RESOURCE GROUPS` shows no `big_query_cpu_second_limit` / `big_query_mem_limit` for that user's group
- Memory usage rises over time without any circuit breaker firing

**Mechanism**: Resource groups without `big_query_*` limits allow individual queries to run indefinitely, consuming arbitrary CPU time and memory. Session-level overrides (e.g., `query_timeout = 86400`) make this worse. Without circuit breakers at the resource group level, the only protection is the BE OOM killer — which is too late and affects all queries on the same node.

**Actions**:

```sql
-- Step D-1: Kill the currently offending query immediately
SHOW PROC '/current_queries';
-- Find: ElapsedTime > expected maximum
KILL QUERY <query_id>;

-- Step D-2: Add big_query limits to the resource group
ALTER RESOURCE GROUP rg_bi SET PROPERTIES (
    "big_query_cpu_second_limit" = "600",      -- 10 minutes CPU time
    "big_query_scan_rows_limit" = "1000000000", -- 1B rows
    "big_query_mem_limit" = "10737418240"       -- 10GB
);

-- Step D-3: Add SQL blacklist for dangerous patterns
ADMIN SET FRONTEND CONFIG ("enable_sql_blacklist" = "true");
-- Block SELECT without WHERE on large tables:
ADD SQLBLACKLIST "select .* from big_table[^w]*;";
SHOW SQLBLACKLIST;

-- Step D-4: Audit-based tuning for big_query_* limits
-- Set limits to 2x of the P99 from audit log (see Phase 2 queries)
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause E — Statistics Collection Saturating BRPC

**Confirm all match**:
- FE log shows BRPC failures correlated with statistics collection schedule
- Query latency degrades during statistics collection windows
- `case-002-rpc-failed-statistics` pattern matches

**Mechanism**: StarRocks statistics collection runs `ANALYZE TABLE` jobs across all tables, generating many parallel RPC calls through the BRPC thread pool. When the pool is saturated, other internal RPCs (query coordination, heartbeat) queue behind statistics RPCs. This manifests as general query degradation or timeout failures that appear unrelated to statistics collection.

**Actions**:

```sql
-- Step E-1: Disable statistics collection immediately (emergency)
ADMIN SET FRONTEND CONFIG ("enable_statistic_collect" = "false");
ADMIN SET FRONTEND CONFIG ("enable_statistic_collect_on_first_load" = "false");
SET GLOBAL analyze_mv = "";  -- v3.3+

-- Step E-2: Verify query latency recovers
-- Wait 2-3 minutes; check SHOW PROC '/current_queries' for improvement

-- Step E-3: Re-enable with reduced concurrency (scheduled off-peak)
ADMIN SET FRONTEND CONFIG ("enable_statistic_collect" = "true");
ADMIN SET FRONTEND CONFIG ("statistic_collect_concurrency" = "1");  -- default 3

-- Step E-4: Schedule statistics collection during off-peak windows
-- Use ANALYZE TABLE ... WITH ASYNC during low-traffic hours
```

→ **Go to Phase 4: Verify Recovery**

---

## Phase 4 — Verify Recovery

```sql
-- Verify queries are classified (not in default_wg)
-- Check audit log for resource_group field after fix
-- Or run a test query and verify:
EXPLAIN VERBOSE SELECT COUNT(*) FROM <table>;
-- Should show ResourceGroup: <your_group>, not default_wg

-- Verify resource group limits are applied
SHOW RESOURCE GROUPS;
-- Confirm big_query_* limits are set for all user groups

-- Verify queue is not full
-- Run: ADMIN SHOW FRONTEND CONFIG LIKE 'query_queue%';
-- Current queue depth should be far below max_queued_queries
```

```bash
# Verify no memory limit exceeded errors
grep "Memory limit exceeded" be/log/be.log | tail -5
# Should be empty or from before the fix

# Verify no queue rejections in FE log
grep "queue is full" fe/log/fe.log | tail -5
# Should be empty or from before the fix

# Verify FE resource group metrics show healthy distribution
curl -s "http://<fe_host>:8030/metrics" | grep "starrocks_fe_query_resource_group_err"
# Error counts should be 0 or decreasing
```

```sql
-- Run a representative big query and verify circuit breaker fires (Cause D fix)
-- The query should be killed after big_query_cpu_second_limit seconds
-- Check: FE log shows "big query killed" for the test query
```

---

## Resource Isolation Config Parameters

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `cpu_core_limit` | — | Soft CPU limit (cores) for resource group | Yes (`ALTER RESOURCE GROUP`) |
| `mem_limit` | — | Hard memory limit (%) per BE for resource group | Yes (`ALTER RESOURCE GROUP`) |
| `concurrency_limit` | — | Max concurrent queries in resource group | Yes (`ALTER RESOURCE GROUP`) |
| `big_query_cpu_second_limit` | — | CPU second circuit breaker per query | Yes (`ALTER RESOURCE GROUP`) |
| `big_query_scan_rows_limit` | — | Scan row count circuit breaker per query | Yes (`ALTER RESOURCE GROUP`) |
| `big_query_mem_limit` | — | Memory circuit breaker per query (bytes) | Yes (`ALTER RESOURCE GROUP`) |
| `query_queue_mem_used_pct_limit` | 0.9 | BE memory usage fraction that triggers queuing | Yes (FE ADMIN SET) |
| `query_queue_concurrency_limit` | -1 | Global concurrent query limit for queuing | Yes (FE ADMIN SET) |
| `query_queue_max_queued_queries` | 1024 | Max queries waiting in queue before rejection | Yes (FE ADMIN SET) |
| `query_queue_pending_timeout_second` | 300 | Max queue wait time before timeout error | Yes (FE ADMIN SET) |
| `enable_sql_blacklist` | false | Enable regex-based SQL blocking | Yes (FE ADMIN SET) |
| `enable_statistic_collect` | true | Enable background statistics collection | Yes (FE ADMIN SET) |
| `statistic_collect_concurrency` | 3 | Concurrent statistics collection jobs | Yes (FE ADMIN SET) |
| `spill_local_storage_dir` | — | Directory for query spill data (BE) | No (be.conf restart) |

---

## Causal Chains

### Chain 1: Unclassified Query Monopolizes BE Resources → Small Queries Starve

```
Large analytical query submitted without resource group assignment
  ↓ observable: SHOW PROC '/current_queries' shows query with no resource_group field or assigned to default_wg
Query consumes all available BE CPU threads and memory (no admission control)
  ↓ observable: BE metrics show CPU utilization near 100%; starrocks_be_resource_group_mem_limit_bytes for default_wg has no effective cap
Concurrent small OLTP queries cannot acquire CPU slots or memory
  ↓ observable: SHOW PROC '/current_queries' shows small queries in RUNNING state with abnormally high elapsed time
Small query latency spikes; P99 rises from milliseconds to seconds
  ↓ observable: application latency dashboards show sudden spike correlated with large query start
```
Interactive queries become unusable during analytical query execution; users report random slowness.

**Trigger conditions**: Resource groups not configured or classifiers not covering all query types; `default_wg` has no CPU/memory limits.

**Break point**: Define resource groups with explicit `cpu_core_limit` and `mem_limit`; create classifiers to route large analytical queries to a dedicated group with lower priority; set limits on `default_wg` as safety net.

---

### Chain 2: Tight Resource Group Mem Limit → Disk Spill → IO Contention → Collateral Slowdown

```
Query assigned to resource group with conservatively set mem_limit (e.g., 2GB)
  ↓ observable: SHOW RESOURCE GROUPS shows mem_limit for the group; query involves sort/hash join exceeding that limit
Intermediate data (sort buffer, hash table) exceeds per-group memory limit
  ↓ observable: BE log "query spill to disk" or "MemTracker exceed limit"; spill directory write IO begins
Disk I/O for spill competes with compaction and tablet operations
  ↓ observable: BE disk throughput metrics show spike; disk latency increases for all I/O on that host
Other queries on same BE experience elevated scan latency due to IO contention
  ↓ observable: SHOW PROC '/current_queries' shows unrelated queries with increased ScanTime; BE disk await time rises
```
Spilling query completes slowly; collateral damage degrades all queries on same BE.

**Trigger conditions**: `mem_limit` set without accounting for operator memory peaks; spill disk shared with data storage; concurrent spill from multiple queries.

**Break point**: Increase resource group `mem_limit` to accommodate peak operator memory; configure dedicated spill directory on separate disk (`spill_local_storage_dir`).

---

### Chain 3: Query Queue Full → Immediate Rejection → Misleading Client Error

```
Resource group reaches max_concurrency; queue enabled with finite max_queue_size
  ↓ observable: FE log shows queries being admitted to queue; information_schema.resource_groups_load_history shows concurrency at limit
Burst of new queries arrives; queue fills to max_queue_size
  ↓ observable: FE log "query rejected: queue is full" for incoming queries beyond capacity
New queries beyond queue capacity rejected immediately with error
  ↓ observable: client receives connection error or SQL exception immediately, not after a wait timeout
Client application interprets immediate rejection as connectivity/crash event, not resource exhaustion
  ↓ observable: application error logs show "connection refused" instead of timeout; no retry logic triggered
```
Users and operators see what appears to be a crash event; root cause (queue full) obscured by immediate error.

**Trigger conditions**: `max_queue_size` too small relative to burst traffic; `max_concurrency` per group too low; no client-side retry logic for resource rejection errors.

**Break point**: Increase `max_queue_size` to absorb burst; tune `max_concurrency` based on `information_schema.resource_groups_load_history` peak usage; implement client-side retry with backoff.

---

## Related Cases

- `case-002-rpc-failed-statistics` — statistics collection saturating brpc; emergency disable pattern
- `case-015-memory-volatility` — governance gap allowing session-level timeout override

---

## Resources

- [Resource Isolation FAQ](https://docs.starrocks.io/docs/faq/resource_isolation_faq/)
