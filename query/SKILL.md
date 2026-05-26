---
name: 01-query
description: "Use when queries are slow, hanging, or returning wrong results. Covers the full FE→BE query hang flow, profile-based bottleneck analysis (scan skew, MERGE time dominance, join OOM, runtime filter), predicate pushdown failures, and bug localization via session variable exclusion. Start here for any query performance complaint on a StarRocks cluster."
version: 2.0.0
category: query
keywords:
- query hang
- slow query
- profile
- scan
- join
- data skew
- runtime filter
- low cardinality
tools:
- jstack
- sort
- find
- grep
- python3
- cut
- cat
- strace
- netstat
- analyze_logs.py
related_cases:
- case-014-scan-skew
- case-015-memory-volatility
- case-003-fe-deadlock
---

# 01 - Query Troubleshooting

Investigation guide for query hangs, slow queries, profile analysis, scan performance,
join performance, and bug localization.

Three root causes account for the vast majority of cases:

- **Cause A** — Network/connection issue (TCP backlog, accept queue saturation)
- **Cause B** — FE lock / GC blocking (deadlock, full GC pause)
- **Cause C** — Scan bottleneck (data skew, no predicate pushdown, rowset accumulation)
- **Cause D** — Join bottleneck (broadcast OOM, missing statistics)
- **Cause E** — Bug localization (wrong results, executor crash)

---

## Metric Taxonomy — Read This First

Before using any metrics, understand the two-layer observability structure:

### FE-side metrics

| Metric / Field | Meaning |
|---|---|
| `fe.audit.log` → `QueryTime` | End-to-end wall clock time for the query (ms) |
| `fe.audit.log` → `ScanRows` | Rows scanned from storage layer |
| `fe.audit.log` → `ScanBytes` | Bytes read from storage layer |
| `fe.audit.log` → `MemCostBytes` | Peak memory allocated on FE side |
| `SHOW PROC '/current_queries'` → `QueryId` | Unique query identifier |
| `SHOW PROC '/current_queries'` → `ConnectionId` | Client connection ID |
| `SHOW PROC '/current_queries'` → `ScanRows` | Rows scanned so far (live) |
| `SHOW PROC '/current_queries'` → `ProcessRows` | Rows processed so far (live) |
| `SHOW PROC '/current_queries'` → `ExecTime` | Elapsed execution time (ms) |
| `SHOW PROC '/current_queries'` → `MemUsageBytes` | Current memory usage (live) |
| Profile → `OLAP_SCAN_NODE` | Per-operator timing; entry via `SHOW PROFILELIST` or FE HTTP UI |

### BE-side metrics

| Metric / Field | Meaning |
|---|---|
| `starrocks_be_tablet_cumulative_max_compaction_score` | Max cumulative compaction score on this BE — high value means rowset accumulation, which inflates scan MERGE time |
| Profile → `MERGE (aggr/union/sort)` | Time spent merging rowsets at scan time; high = compaction lag |
| Profile → `IOTime` | Disk I/O time at storage layer |
| Profile → `JoinRuntimeFilterEvaluate` | Number of RuntimeFilters applied; 0 = no filter pushdown |

### How to retrieve metrics

**Option 1 — FE audit log (historical)**

```bash
# Find slowest queries by QueryTime
grep 'slow_query' fe.audit.log | cut -d '=' -f 6 | cut -d '|' -f 1 | sort -g | tail -20

# Find high-memory queries
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "MemCostBytes" 3 fe.audit.log

# Audit log field order (pipe-delimited):
# Timestamp | ClientIP | User | QueryId | QueryTime | ScanBytes | ScanRows | ReturnRows | StmtId | IsQuery | FeIp | Stmt | Digest | PlanCpuCost | PlanMemCost | PendingTimeMs | MemCostBytes
```

**Option 2 — SQL (live, in-flight queries)**

```sql
-- View all running queries with resource usage
SHOW PROC '/current_queries';
-- Key columns: QueryId, ConnectionId, Database, User, ScanRows, ProcessRows, ExecTime, MemUsageBytes

-- Kill a specific query
KILL QUERY '<connection_id>';
```

**Option 3 — Profile (per-query operator breakdown)**

```sql
-- Enable profile collection before running the query
SET is_report_success = true;
-- Run query, then view profile:
-- FE HTTP UI: http://<fe_ip>:8030 → Queries → Profile

-- Or list recent profiles and analyze
SHOW PROFILELIST;
ANALYZE PROFILE FROM '<profile_id>';
```

**Option 4 — BE compaction metrics (scan MERGE time signal)**

```bash
# High cumulative score → rowset accumulation → slow scan MERGE phase
curl -s "http://<be_ip>:8040/metrics" | grep "tablet_cumulative_max_compaction_score"

# Loop all BEs
for be in 10.0.0.1 10.0.0.2 10.0.0.3; do
  echo "=== $be ==="
  curl -s "http://$be:8040/metrics" | grep "tablet_cumulative_max_compaction_score"
done
```

---

**Key rules for interpretation**:

1. Always start with `fe.audit.log` for historical slow queries — `QueryTime` shows end-to-end latency; correlate `ScanBytes` vs `ReturnRows` to spot pushdown failures.
2. `SHOW PROC '/current_queries'` gives live in-flight state; `ExecTime` is wall-clock elapsed — compare against `query_timeout` setting to judge urgency.
3. Profile `OLAP_SCAN_NODE Active` time variance across instances reveals data skew — one instance 10× slower than peers is the primary signal.
4. Profile `MERGE` time as a fraction of `OLAP_SCAN_NODE Active` indicates rowset accumulation — above 50% means compaction is lagging.
5. If `fe.audit.log` shows no entry for the query ID, the FE never received the request — go to Cause A (network/connection).

---

## Query Issue Type Reference

| Symptom | Points to Cause | Quick Confirm |
|---|---|---|
| Client hangs, no query ID in `fe.audit.log` | Cause A — Network/connection | `netstat -s \| grep TCPBacklogDrop` non-zero |
| Query ID in `fe.log` but no response; jstack shows lock wait | Cause B — FE lock / GC | `jstack <fe_pid>` → `ConnectProcessor` waiting on same lock |
| Query ID found; one `OLAP_SCAN_NODE` instance 10× slower | Cause C — Data skew | Profile `Active` time variance; `SHOW TABLET` DataSize variance |
| `PushdownPredicates` = 0 in profile despite WHERE clause | Cause C — No pushdown | Check column type mismatch or function wrapping on filter |
| `MERGE` time > 50% of scan time | Cause C — Rowset accumulation | `be_tablets.num_rowset` > 50 on scanned table |
| `HASH_JOIN_NODE BuildRows` very large; OOM or spill | Cause D — Broadcast OOM | Profile `EXCHANGE_NODE` → huge right table transfer |
| Missing statistics; planner chose wrong join type | Cause D — Missing stats | `SELECT * FROM _statistics_.table_statistic_v1 WHERE table_name LIKE '%<name>%'` empty |
| Wrong results or BE crash | Cause E — Bug | Collect query dump, `EXPLAIN COSTS`, disable features one by one |

---

## Phase 1 — Confirm Query Is Problematic

### Step 1.1 — Check if FE received the request

```bash
# Search for the query registration in FE log
grep "register query id" fe.log | grep "<query_id>"
```

- Found → FE received the request → proceed to Step 1.2
- Not found → FE never received it → **Cause A signal** → go to Phase 3, Cause A

### Step 1.2 — Check live in-flight state

```sql
-- Is the query still running? How long?
SHOW PROC '/current_queries';
-- ExecTime vs query_timeout: if ExecTime > 80% of timeout, it will likely fail
-- MemUsageBytes growing rapidly → potential OOM → Cause D signal
```

### Step 1.3 — Check audit log for historical pattern

```bash
# Did this query or similar queries consistently run slow?
grep "<query_id_prefix>" fe.audit.log | awk -F'|' '{print $5}'
# QueryTime column — multiple slow entries → structural problem, not transient

# Check if query even completed (vs hang / timeout)
grep "slow_query" fe.audit.log | tail -30
```

**Phase 1 result → route by cause**:

| Finding | Next step |
|---|---|
| No `register query id` | Phase 3, Cause A |
| FE log shows query received; jstack shows `ConnectProcessor` lock | Phase 3, Cause B |
| Query running slow; profile shows scan variance | Phase 2 (profile analysis) |
| Query running slow; profile shows large join build | Phase 2 (profile analysis) |

---

## Phase 2 — Locate Bottleneck by Profile

### Step 2.1 — Enable and collect profile

```sql
SET is_report_success = true;
-- Re-run the slow query
-- Then: http://<fe_ip>:8030 → Queries → Profile
-- Or:
SHOW PROFILELIST;
ANALYZE PROFILE FROM '<profile_id>';
```

### Step 2.2 — Read OLAP_SCAN_NODE metrics

Key metrics in `OLAP_SCAN_NODE`:

| Metric | Meaning |
|---|---|
| `BytesRead` | Data volume read from tablets |
| `RowsReturned` | Rows returned after filtering |
| `RowsReturnedRate` | Per-node return rate — variance indicates skew |
| `TabletCount` | Number of tablets scanned |
| `MERGE (aggr/union/sort)` | Rowset merge time — high = compaction lag |
| `IOTime` | Disk I/O time |
| `PushdownPredicates` | Count of predicates pushed to storage |
| `RawRowsRead` | Total raw rows before filtering |
| `ZoneMapIndexFilterRows` / `BloomFilterFilterRows` / `BitmapIndexFilterRows` | Rows eliminated by indexes |

### Step 2.3 — Identify the bottleneck type

**Scan bottleneck signals** (→ Cause C):
```
MERGE time > 50% of OLAP_SCAN_NODE Active   → rowset accumulation
One instance Active >> peers (10x)           → data skew
PushdownPredicates = 0                       → predicate pushdown failed
ZoneMapIndexFilterRows = 0                   → index not effective
```

**Join bottleneck signals** (→ Cause D):
```
HASH_JOIN_NODE BuildTime high               → right table too large
HASH_JOIN_NODE BuildRows very large         → consider shuffle instead of broadcast
EXCHANGE_NODE time dominates                → large table broadcast
JoinRuntimeFilterEvaluate = 0              → no runtime filter applied
```

### Step 2.4 — Profile time discrepancy

When total Profile time significantly exceeds BE Active time:

1. Check `fe.warn.log` for retry logs.
2. Primary Key model (older versions): lock acquisition timeout — look for gap between prepare and open.
3. Optimizer too slow — verify with `explain`; or jstack to see if stuck in plan generation.
4. Import lock interference — import holds DB lock, blocking query lock acquisition.
5. FE Full GC — check `fe.gc.log` timestamps.

---

## Phase 3 — Take Action by Cause

### Cause A — Network / Connection Issue

**Confirm all match**:
- No `register query id` entry in `fe.log` for the query
- `netstat -s | grep TCPBacklogDrop` shows non-zero and growing
- `ss -lnt` shows `Recv-Q` ≈ backlog limit on FE port 9030

**Mechanism**: When connection burst exceeds the OS TCP listen queue (controlled by `somaxconn`), new SYN packets are silently dropped. The FE `ConnectProcessor` never sees the connection and logs nothing. The client receives a connection timeout with no error from FE.

```bash
# Confirm backlog overflow
netstat -s | grep -i LISTEN
netstat -s | grep TCPBacklogDrop
cat /proc/sys/net/ipv4/tcp_abort_on_overflow
```

Fix:
- Set `tcp_abort_on_overflow` to 1 (returns RST instead of silent drop — easier to diagnose)
- Increase `somaxconn` (at least 1024)
- Adjust FE parameters: `mysql_nio_backlog_num`, `http_backlog_num`, `thrift_backlog_num` (must be ≥ `somaxconn`)
- Restart FE after parameter change

→ **Go to Phase 4 to verify recovery**

---

### Cause B — FE Lock / GC Blocking

**Confirm all match**:
- `register query id` present in `fe.log` but query never dispatched to BE
- `jstack <fe_pid>` shows `ConnectProcessor` threads waiting on same lock object address
- OR `fe.gc.log` shows Full GC events aligned with the hang window

**Mechanism (lock)**: Two query threads acquire FE DB locks in different orders, creating a circular wait. All subsequent queries needing either lock queue behind blocked threads. FE heartbeat stays alive but no fragments are dispatched.

**Mechanism (GC)**: Full GC pauses the JVM for seconds or minutes, freezing all FE threads. All pending queries appear hung during the pause.

```bash
# Capture jstack (repeat 3x, 10 seconds apart)
jstack <fe_pid> > /tmp/fe_jstack_$(date +%s).log

# Look for ConnectProcessor threads waiting on same lock
grep -A 30 "ConnectProcessor" /tmp/fe_jstack_*.log

# Check GC log for Full GC during the hang window
grep "Full GC" fe.gc.log | tail -20
```

Action: capture multiple jstack dumps, then restart FE.

```bash
# Check if BE received the query (rules out FE-only issue)
grep "Prepare(): query_id=<target_query_id>" be.log
```

If BE received it → issue is on BE side → check pstack / execution thread pool (Step 1.6 in original guide).

→ **Go to Phase 4 to verify recovery**

---

### Cause C — Scan Bottleneck

#### C1: Data Skew

**Confirm all match**:
- Profile shows one `OLAP_SCAN_NODE` instance Active time 10× higher than peers
- `SHOW TABLET FROM <table>` shows DataSize variance > 10× across tablets

**Mechanism**: Low-cardinality bucket key (e.g., `status`, `region`) concentrates majority of rows in one or few tablets. All queries on hot range land on one scan instance. BE CPU on hot node saturates; others idle. Query latency is dominated by the slowest straggler scan instance.

```bash
# Compare Active times across OLAP_SCAN_NODE instances in profile
# Pattern indicating skew:
# OLAP_SCAN_NODE (id=0):(Active: 4m50s)  <- skewed
# OLAP_SCAN_NODE (id=0):(Active: 250ms)
# OLAP_SCAN_NODE (id=0):(Active: 131ms)
```

```sql
-- Check DataSize distribution across tablets
SHOW TABLET FROM <table>;

-- Standard deviation check via be_tablets
SELECT be_id, COUNT(*) AS tablet_count,
       MAX(data_size) AS max_size, MIN(data_size) AS min_size,
       ROUND(STDDEV(data_size) / AVG(data_size), 2) AS cv
FROM information_schema.be_tablets bt
JOIN information_schema.tables_config tc ON bt.table_id = tc.table_id
WHERE tc.table_name = '<table_name>'
GROUP BY be_id;
-- CV > 1.0 indicates severe skew
```

Fix: change the bucket key to a column with higher cardinality and more even distribution.

```sql
-- v3.3+: online redistribution
ALTER TABLE <table_name> DISTRIBUTED BY HASH(<new_high_cardinality_column>);
```

#### C2: Predicate Pushdown Failure

**Confirm all match**:
- Profile shows `PushdownPredicates = 0` despite WHERE clause on the query
- `RawRowsRead` ≈ total table rows (no filtering at storage layer)

**Mechanism**: If the filter column type mismatches the table column type, or a function wraps the left side of the predicate, the storage layer cannot apply the filter. All rows must be read into the compute layer before filtering.

Common causes:
1. Type mismatch — filter column type doesn't match the table column type
2. Functions on the left side — e.g., `WHERE date_format(dt, '%Y%m') = '202301'` prevents pushdown

```sql
-- Bad: function on left side prevents pushdown
WHERE date_format(dt, '%Y%m') = '202301'

-- Good: range predicate on column directly
WHERE dt BETWEEN '2023-01-01' AND '2023-01-31'
```

#### C3: Rowset Accumulation (MERGE bottleneck)

**Confirm all match**:
- Profile shows `MERGE` time > 50% of `OLAP_SCAN_NODE Active`
- `be_tablets.num_rowset` > 50 on tablets being scanned

**Mechanism**: Each import creates one rowset. When import rate exceeds compaction throughput, rowsets accumulate. At query time, the storage layer must MERGE all rowsets in-memory — MERGE time rises linearly with rowset count. Queries slow 5–20× before any error occurs.

```sql
-- Check rowset accumulation on target table
SELECT bt.be_id, bt.tablet_id, bt.num_rowset,
       ROUND(bt.data_size / 1024 / 1024, 1) AS data_size_mb,
       tc.table_name
FROM information_schema.be_tablets bt
JOIN information_schema.tables_config tc ON bt.table_id = tc.table_id
WHERE tc.table_name = '<table_name>'
ORDER BY bt.num_rowset DESC
LIMIT 10;
```

Fix: tune compaction (see compaction skill); reduce import frequency.

```sql
-- Trigger manual compaction on the problem tablet
-- (requires knowing be_ip from SHOW BACKENDS)
-- curl -XPOST "http://<be_ip>:8040/api/compact?compaction_type=cumulative&tablet_id=<tablet_id>"

-- Routine Load: increase batch interval to reduce rowset creation rate
ALTER ROUTINE LOAD FOR <job_name>
PROPERTIES ("max_batch_interval" = "60", "desired_concurrent_number" = "3");
```

→ **Go to Phase 4 to verify recovery**

---

### Cause D — Join Bottleneck

#### D1: Broadcast OOM / Oversized Right Table

**Confirm all match**:
- Profile `HASH_JOIN_NODE BuildRows` very large (millions+)
- Profile `EXCHANGE_NODE` time dominates and data sent equals full right table × BE count
- BE log shows `Memory exceed limit` or `SpillBytes > 0`

**Mechanism**: Planner chooses broadcast join when right table size is underestimated by stale statistics. Each BE instance materializes a full copy of the right table in memory. Memory pressure triggers spill or OOM. Query fails or runs 100× slower due to spill I/O.

```sql
-- Force shuffle join to avoid broadcast OOM
SELECT a.x, b.y FROM table_a a JOIN [shuffle] table_b b ON a.id = b.id;

-- Or force small table to right side with broadcast hint
SELECT a.x, b.y FROM large_table a JOIN [broadcast] small_table b ON a.id = b.id;
```

#### D2: Missing Statistics

**Confirm all match**:
- Join type is suboptimal (e.g., Broadcast when both tables are large)
- `_statistics_.table_statistic_v1` has no entries for one or both join tables

**Mechanism**: Without statistics, the CBO optimizer cannot estimate cardinality correctly and may choose broadcast when shuffle is correct, or vice versa. RuntimeFilter may also fail to activate.

```sql
-- Check statistics existence
SELECT * FROM _statistics_.table_statistic_v1
WHERE table_name LIKE '%<table_name>%';

-- Collect statistics
ANALYZE TABLE <table_name>;

-- Check RuntimeFilter effectiveness in profile
-- JoinRuntimeFilterEvaluate > 0 means filter applied
-- JoinRuntimeFilterInputRows vs OutputRows shows reduction ratio
```

Join type reference:

| Join Type | Mechanism | When Used |
|---|---|---|
| Broadcast Join | Right table sent to all left table nodes | Right table is small |
| Shuffle Join | Both tables shuffled by join key | Both tables are large |
| Colocate Join | Local join, no network transfer | Tables share colocate group with same bucket key |
| Bucket Shuffle Join | Only right table shuffled to left table nodes | Join column is left table's bucket key |
| Replicated Join | Right table replicated on every BE | Right table replica count equals BE count |

```sql
-- Colocate join setup (requires same colocate_with group)
CREATE TABLE t1 (...) DISTRIBUTED BY HASH(id) PROPERTIES ("colocate_with" = "group1");
CREATE TABLE t2 (...) DISTRIBUTED BY HASH(id) PROPERTIES ("colocate_with" = "group1");
```

→ **Go to Phase 4 to verify recovery**

---

### Cause E — Bug Localization

**Confirm all match**:
- Query produces wrong results, or BE crashes/panics
- Problem is reproducible or consistently appears on specific query patterns

**Mechanism**: Optimizer or executor bugs — nullable info incorrect, Cast type mismatch, predicate lost or pushed incorrectly, Runtime Filter processing errors, Hash Distribution issues.

#### Quick Exclusion Switches

Disable features via session variables to narrow scope:

| Feature | How to disable |
|---|---|
| Low-cardinality optimization | `SET cbo_enable_low_cardinality_optimize = false;` |
| Pipeline engine | `SET enable_pipeline_engine = false;` |
| Expression pushdown to storage | `SET enable_column_expr_predicate = false;` |
| Replication Join | `SET cbo_enable_replicated_join = false;` |
| Local Runtime Filter | `SET hash_join_push_down_right_table = 0;` |
| Global Runtime Filter | `SET enable_global_runtime_filter = false;` |
| Streaming pre-aggregation | `SET streaming_preaggregation_mode = force_preaggregation;` |
| Concurrent plan serialization | `SET enable_plan_serialize_concurrently = false;` |

#### 9-Setting Crash Quick Disable (v3.5+)

When queries crash the BE, use binary exclusion to isolate the culprit:

```sql
SET disable_join_reorder = true;
SET enable_global_runtime_filter = false;
SET enable_query_cache = false;
SET cbo_enable_low_cardinality_optimize = false;
SET cbo_cte_reuse_rate = 0;
SET enable_filter_unused_columns_in_scan_stage = false;
SET GLOBAL enable_pipeline_event_scheduler = false;
SET GLOBAL enable_push_down_pre_agg_with_rank = false;
SET GLOBAL enable_partition_hash_join = false;
-- For materialized view issues:
SET enable_sync_materialized_view_rewrite = false;
```

Collect for engineering: table DDL, `EXPLAIN COSTS` plan, query dump.

```sql
-- General approach
-- 1. Try to reproduce — collect core stack trace, EXPLAIN COSTS, statistics (Query Dump), Profile.
-- 2. If not reproducible — reason from stack traces; diff code changes between versions;
--    suspect immature features first.
```

Common bug causes by module:

- **Optimizer**: Nullable info incorrect, Cast type mismatch, predicate lost or pushed incorrectly, Limit lost, single-phase aggregation used incorrectly, Decimal V3 bugs, View-to-SQL conversion issues.
- **Scheduler**: Related to Bucket Shuffle Join / Colocate Join / Replication Join — disable these and verify with Broadcast/Shuffle Join.
- **Executor — Scan**: Delete handling errors, ZoneMap filtering errors, Char type storage/compute length mismatch, Chunk size exceeds limit.
- **Executor — Aggregate**: Streaming processing errors (check `convert_to_serialize_format`), Nullable signature mismatch.
- **Executor — Join**: Runtime Filter processing errors, Hash Distribution issues (symptom: unstable results).

→ **Go to Phase 4 to verify recovery**

---

## Phase 4 — Verify Recovery

```sql
-- Confirm query latency returned to baseline
-- Run the same query and check QueryTime in audit log
grep "<query_pattern>" fe.audit.log | awk -F'|' '{print $1, $5}' | tail -10
-- QueryTime should be back to expected baseline

-- Confirm no queries hanging
SHOW PROC '/current_queries';
-- Should show low ExecTime for all active queries

-- Confirm profile improvement (re-run with profile enabled)
SET is_report_success = true;
-- Re-run query and compare:
-- OLAP_SCAN_NODE Active: no longer one instance >> peers
-- MERGE time: below 20% of scan time (if Cause C)
-- HASH_JOIN_NODE BuildRows: within expected range (if Cause D)
```

```bash
# For Cause A: confirm no new TCP backlog drops
netstat -s | grep TCPBacklogDrop
# Value should be stable (not increasing)

# For Cause B: confirm FE threads are not stuck
jstack <fe_pid> | grep -c "BLOCKED"
# Should be 0 or very low

# For Cause C (rowset): confirm compaction is catching up
curl -s "http://<be_ip>:8040/metrics" | grep "tablet_cumulative_max_compaction_score"
# Should trend downward; target < 100
```

---

## Key Session Variables

| Variable | Default | Description | Dynamic |
|---|---|---|---|
| `is_report_success` | false | Enable profile collection for this session | Yes |
| `query_timeout` | 300 | Query timeout in seconds | Yes |
| `mem_limit` | 80% | Per-query memory limit on BE | Yes |
| `broadcast_row_limit` | 15000000 | Max rows before planner avoids broadcast join | Yes |
| `enable_pipeline_engine` | true | Use pipeline execution engine | Yes |
| `enable_global_runtime_filter` | true | Enable global RuntimeFilter | Yes |
| `cbo_enable_low_cardinality_optimize` | true | Low-cardinality dict optimization | Yes |
| `hash_join_push_down_right_table` | true | Push local runtime filter to right table | Yes |
| `cbo_enable_replicated_join` | false | Allow replicated join strategy | Yes |
| `enable_column_expr_predicate` | true | Push expressions to storage layer | Yes |
| `disable_join_reorder` | false | Disable join reorder optimization | Yes |
| `streaming_preaggregation_mode` | auto | Pre-aggregation before shuffle | Yes |
| `enable_query_cache` | true | Enable query result cache | Yes |

---

## Common Issues Quick Reference

| Issue | Cause | Fix |
|---|---|---|
| Scan node slow on a single instance | Data skew on a hot bucket key | Re-bucket with higher cardinality column |
| MERGE phase dominates scan time | Too many rowsets per tablet | Tune compaction; reduce import frequency |
| Broadcast join blows up memory | Right table too large | Use `[shuffle]` hint |
| RuntimeFilter not active | Missing statistics or CBO disabled | `ANALYZE TABLE`; enable CBO |
| `version does not exist` mid-query | FE deadlock blocked tablet report; BE recycled versions | Capture jstack, restart FE |

---

## Related Cases

- `case-014-scan-skew` — bucket key optimization on aggregate model
- `case-015-memory-volatility` — session-level timeout override
- `case-003-fe-deadlock` — FE LockManager deadlock causing version-not-found

---

## Causal Chains

### Chain 1: FE TCP Accept Queue Saturation

```
Client connection attempt
  ↓ observable: netstat -s | grep TCPBacklogDrop shows non-zero and growing
OS TCP backlog exhausted (listen queue full)
  ↓ observable: ss -lnt shows Recv-Q ≈ backlog limit on FE port 9030
FE ConnectProcessor cannot accept new sockets
  ↓ observable: fe.log shows no new "register query id" entries despite client retries
Client TCP SYN silently dropped
```
Client receives connection timeout with no error message from FE.

**Trigger conditions**: Burst of short-lived queries; `qe_max_connection` not tuned; `somaxconn` below FE backlog params.

**Break point**: Increase `net.core.somaxconn`; raise FE `mysql_nio_backlog_num`, `http_backlog_num`, `thrift_backlog_num` to ≥ `somaxconn`; add client-side connection pooling.

---

### Chain 2: FE Fair-Lock Deadlock → All Queries Hang

```
Two query threads acquire FE DB locks in different orders
  ↓ observable: jstack shows thread A holds lock-X waiting for lock-Y; thread B holds lock-Y waiting for lock-X
Circular wait: both threads park indefinitely
  ↓ observable: jstack "parking to wait for" — same lock address in two "Locked ownable synchronizers" blocks
All subsequent queries needing either lock queue behind blocked threads
  ↓ observable: SHOW PROC '/current_queries' shows dozens of queries in PENDING with identical wait duration
FE query scheduler stalls; no new fragments dispatched
```
All in-flight queries hang; FE heartbeat still alive but no results produced.

**Trigger conditions**: Concurrent DDL + DML on shared metadata locks; new lock nesting introduced without global ordering.

**Break point**: Enforce canonical lock acquisition order (by DB ID ascending) in `LockManager`; short-term: FE rolling restart.

---

### Chain 3: High-Frequency Import → Rowset Accumulation → Scan MERGE Bottleneck

```
Many small imports land many rowsets per tablet
  ↓ observable: SHOW TABLET <id> shows RowsetCount > 50; compaction score elevated in BE metrics
Compaction cannot keep up; cumulative rowset count exceeds threshold
  ↓ observable: BE log "reach cumulative compaction score limit"; compaction bytes/sec < ingest rate
Scan node must MERGE hundreds of rowsets at query time
  ↓ observable: Profile shows MERGE time dominates OLAP_SCAN_NODE Active; BytesRead large vs result set
Query latency multiplies proportionally to rowset count
```
Queries 5–20x slower than baseline; worsens progressively until compaction catches up.

**Trigger conditions**: Import rate > compaction throughput; tablet count too low; `cumulative_compaction_num_threads_per_disk` under-provisioned.

**Break point**: Reduce import frequency or batch imports; increase `cumulative_compaction_num_threads_per_disk`; run `ALTER TABLE COMPACT`.

---

### Chain 4: Low-Cardinality Bucket Key → Data Skew → Straggler Scan Instance

```
Data distribution skewed: majority of rows hash to one or few tablets
  ↓ observable: SHOW TABLET FROM <table> shows DataSize variance > 10x across tablets
All queries on hot range land on the same scan instance
  ↓ observable: Profile shows one OLAP_SCAN_NODE instance Active time 10x higher than peers
BE CPU on hot node saturates; others idle
  ↓ observable: top on hot BE shows high CPU; other BEs idle
Query waits for the slowest straggler scan instance
```
Overall latency dominated by a single slow tablet; adding parallelism or nodes provides no relief.

**Trigger conditions**: Low-cardinality bucket key (status, region); skewed business-domain data.

**Break point**: Redesign bucket key to high-cardinality column; use `ALTER TABLE ... DISTRIBUTED BY HASH(new_key)` (v3.3+).

---

### Chain 5: Oversized Broadcast Join → Memory Spike → OOM / Extreme Slowness

```
Planner chooses broadcast join; right table size underestimated by stale statistics
  ↓ observable: Profile shows EXCHANGE_NODE sending full right table to every BE instance
Each BE instance materializes full copy of right table in memory
  ↓ observable: SHOW PROC '/current_queries' shows memUsageBytes climbing rapidly
Memory pressure triggers spill or OOM on BE
  ↓ observable: BE log "Memory exceed limit"; Profile shows SpillBytes > 0 or query cancelled
Query fails or runs 100x slower due to spill I/O
```
Client receives `Memory limit exceeded` or query runs for minutes before cancellation.

**Trigger conditions**: Right table cardinality underestimated; `broadcast_row_limit` not set; statistics stale.

**Break point**: Run `ANALYZE TABLE`; force `[SHUFFLE]` hint when right table is large; set `broadcast_row_limit` as safety cap.

---

## Resources

- [SQL FAQ](https://docs.starrocks.io/docs/faq/Sql_faq/)
- [Query Cache documentation](https://docs.starrocks.io/docs/using_starrocks/query_cache/)
