---
name: import
description: "Use when import jobs are slow, timing out, or failing — Stream Load, Broker Load, Routine Load, INSERT INTO, Flink connector. Covers write-slow triage (async_delta_writer / memtable_flush / segment_replicate_sync thread pools), publish timeout on PK tables, RPC Failed errors, Reached timeout auto-diagnostics (v3.4+), and load profile analysis. Also covers read-slow causes and Kafka partition bottlenecks."
version: 2.0.0
category: import
keywords:
- import
- broker load
- stream load
- routine load
- RPC failed
- publish timeout
- primary key
- flink connector
- profile
- reached timeout
- slow import
tools:
- curl
- jstack
- grep
- netstat
- tcpdump
related_cases:
- case-001-broker-load-backlog
- case-002-rpc-failed-statistics
- case-007-memory-tracking-leak
- case-009-stream-load-stuck
---

# Import Troubleshooting

Investigation guide for import slowness, timeouts, RPC failures, publish timeouts,
Primary Key model tuning, and load profile analysis.

Six root causes account for the vast majority of cases:

- **Cause A** — Write slow: thread pool bottleneck (async_delta_writer / memtable_flush saturated)
- **Cause B** — Write slow: BRPC / network issue (connection backlog, packet loss)
- **Cause C** — Write slow: PK index rebuild during clone / decommission
- **Cause D** — Publish timeout: compaction lag on PK table
- **Cause E** — Read slow: source-side bottleneck (Kafka partition count, file IO, HTTP client)
- **Cause F** — RPC Failed: statistics collection conflict saturating BRPC

---

## Metric Taxonomy — Read This First

Before using any metrics, understand the write pipeline and where each metric fits:

### Thread pool metrics (BE-side, per-node)

| Pool | Metrics | Meaning |
|---|---|---|
| `async_delta_writer` | `pending` | Queue wait time — high = pool size insufficient |
| `async_delta_writer` | `execute` | Task execution time — contains wait_flush + wait_replica |
| `async_delta_writer` | `wait_flush` | Wait for memtable flush — high → analyze memtable_flush pool |
| `async_delta_writer` | `wait_replica` | Wait for secondary replica sync — high → analyze segment_replicate |
| `async_delta_writer` | `pk_preload` | PK index rebuild time — high → set `skip_pk_preload = true` |
| `memtable_flush` | `pending` | Queue wait time |
| `memtable_flush` | `execute` | Total flush time |
| `memtable_flush` | `io` | IO portion of flush — high = disk or S3 bottleneck |
| `memtable_flush` | `rate` | Flush rate (tasks/sec) |
| `segment_replicate_sync` | `pending` / `execute` | Replica sync queue wait and processing time |
| `segment_flush` | `pending` / `execute` / `io` | Secondary disk flush metrics |

### BRPC metrics (BE-side, per interface)

| Metric | Meaning |
|---|---|
| `total` / `used` | Total / in-use BRPC threads — if `used` ≈ `total`, BRPC is saturated |
| `latency-avg` / `latency-99` | Interface latency — high P99 = server-side processing slow |
| `Brpc Processing Requests` | In-flight RPC count — 0 during timeout = network or BRPC issue |

Key interfaces: `tablet_writer_open`, `tablet_writer_add_chunks`, `tablet_writer_add_segment`

### Import state metrics (FE-side, SQL)

Key fields from `information_schema.loads`:

| Field | Meaning |
|---|---|
| `STATE` | PENDING / BEGIN / LOADING / PREPARED / COMMITTED / FINISHED / CANCELLED |
| `PROGRESS` | ETL and LOADING phase progress percentage |
| `SCAN_ROWS` / `SCAN_BYTES` | Source data read |
| `SINK_ROWS` | Rows successfully written |
| `CREATE_TIME` / `LOAD_START_TIME` / `LOAD_COMMIT_TIME` / `LOAD_FINISH_TIME` | Timestamps for phase duration |
| `ERROR_MSG` | Error message (NULL if none) |
| `PROFILE_ID` | Profile ID for `ANALYZE PROFILE FROM 'profile_id'` |

### How to retrieve metrics

**Option 1 — BRPC metrics endpoint (per-BE)**

```bash
# View all import-related thread pool metrics
curl -s "http://<be_ip>:8060/vars" | grep -E "async_delta_writer|memtable_flush|segment_replicate|segment_flush"

# View BRPC thread utilization
curl -s "http://<be_ip>:8060/vars" | grep -E "brpc_worker|bthread|tablet_writer"

# Loop all BEs
for be in 10.0.0.1 10.0.0.2 10.0.0.3; do
  echo "=== $be ==="
  curl -s "http://$be:8060/vars" | grep -E "async_delta_writer_pending|async_delta_writer_execute|memtable_flush_io"
done
```

**Option 2 — SQL (import state)**

```sql
-- All running and recent import tasks
SELECT ID, LABEL, STATE, TYPE, PROGRESS, SCAN_BYTES, SINK_ROWS,
       LOAD_START_TIME, LOAD_FINISH_TIME, ERROR_MSG
FROM information_schema.loads
WHERE STATE NOT IN ('FINISHED', 'CANCELLED')
ORDER BY CREATE_TIME DESC;

-- Historical import throughput by minute
SELECT date_trunc('minute', load_finish_time) AS t,
       count(*) AS tpm, sum(SCAN_BYTES) AS scan_bytes, sum(sink_rows) AS sink_rows
FROM _statistics_.loads_history
GROUP BY t ORDER BY t DESC LIMIT 10;
```

**Option 3 — FE transaction log (write vs publish duration)**

```bash
# Find write vs publish duration by label or txn_id
grep "<label_or_txn_id>" fe.log | grep "finishTransaction"
# Log format: "write cost: 243ms ... publish total cost: 154ms"
# write cost >> publish cost → slow write phase (Cause A/B/C)
# publish cost >> write cost → slow publish phase (Cause D)
```

**Option 4 — BE auto-diagnostic profile (v3.4+)**

```bash
# Reached timeout profile (v3.4+: auto-logged on timeout)
grep "profile=" be.WARNING
# Example: tablet writer add chunk timeout. txn_id=1691, cost=16728ms, timeout=16500ms, profile=xxx

# Stack trace (v3.5+)
grep "diagnose stack trace, id:" be.INFO
# Extract the id, then:
grep "DIAGNOSE <id> -" be.INFO > stack_trace.log
```

---

**Key rules for interpretation**:

1. Start with the FE transaction log (`finishTransaction`) to split write cost vs publish cost — this tells you which phase to focus on.
2. If `async_delta_writer.pending` is high, the pool is too small — increase `number_tablet_writer_threads`. If `execute` is high with `pending` low, the bottleneck is within the task (check `wait_flush`, `wait_replica`, `pk_preload`).
3. If BRPC `used` ≈ `total` and `Brpc Processing Requests` is near-zero during a timeout, the BRPC layer itself is the bottleneck — not the storage layer.
4. PK tables have an additional `pk_preload` sub-phase in `async_delta_writer.execute` — this alone can cause minutes-long write stalls during clone or decommission operations.

---

## Import Slow Signal Reference

| Signal / Error | Points to Cause | Phase |
|---|---|---|
| `Timeout by txn manager` | Cause A or E — write or read slow | Write or Read |
| `[E1008]Reached timeout` | Cause A or B — storage write timeout (brpc between Coordinator and Executor) | Write |
| `publish timeout` | Cause D — PK compaction lag | Publish |
| `RPC Failed` sending plan fragment | Cause F — BRPC saturated by statistics collection | Write setup |
| Routine Load task lag grows | Cause E — Kafka partition bottleneck | Read |
| `async_delta_writer.pending` non-zero and growing | Cause A — thread pool exhausted | Write |
| `async_delta_writer.pk_preload` high | Cause C — PK index rebuild | Write |
| `memtable_flush.io` ≈ `memtable_flush.execute` | Cause A — disk or S3 IO saturation | Write |
| BRPC `used` ≈ `total`, `Processing Requests` = 0 | Cause B — BRPC network saturation | Write |
| `starrocks_be_publish_version_queue_count` growing | Cause D — publish queue backlog | Publish |

---

## Terminology

| Term | Definition |
|---|---|
| Import Job | Continuous import operations: Routine Load Job, Pipe Job |
| Import Task | One-time import task corresponding to a transaction: Broker Load, Stream Load, Spark Load, Insert Into. Routine Load/Pipe internally generate continuous Import Tasks |

---

## Phase 1 — Confirm Import Is Behind

### Step 1.1 — Check import state and timestamps

```sql
-- Find slow / stuck imports
SELECT ID, LABEL, STATE, TYPE,
       TIMESTAMPDIFF(SECOND, LOAD_START_TIME, NOW()) AS elapsed_sec,
       PROGRESS, SCAN_BYTES, SINK_ROWS, ERROR_MSG
FROM information_schema.loads
WHERE STATE IN ('LOADING', 'PREPARED', 'COMMITED')
ORDER BY elapsed_sec DESC;
```

### Step 1.2 — Determine which phase is slow

```bash
# Split write vs publish duration
grep "<label_or_txn_id>" fe.log | grep "finishTransaction"
# "write cost: Xms ... publish total cost: Yms"
```

| Phase timing | Next step |
|---|---|
| `write cost` >> expected | Phase 2 (identify write bottleneck) |
| `publish total cost` >> expected | Phase 3, Cause D |
| Both normal but overall slow | Phase 2 (check read phase) |
| Error: `RPC Failed` | Phase 3, Cause F |

### Step 1.3 — Scan thread pool health

```bash
# Quick health check on all pools
curl -s "http://<be_ip>:8060/vars" \
  | grep -E "(async_delta_writer|memtable_flush|segment_replicate_sync)\.(pending|execute|io)"
```

If any `pending` metric is non-zero and sustained → pool is saturated → proceed to Phase 2.

---

## Phase 2 — Identify Slow Phase (Read vs Write vs Publish)

### Step 2.1 — Enable and read load profile

```sql
-- Session variable (Broker Load / INSERT INTO)
SET enable_profile = true;
-- Auto-enable for long imports (>60s)
SET big_query_profile_threshold = 60s;

-- Table property (Stream Load / Routine Load)
ALTER TABLE <table_name> SET ("enable_load_profile" = "true");

-- View profiles
SHOW PROFILELIST;
ANALYZE PROFILE FROM '<profile_id>';
```

### Step 2.2 — Profile interpretation

| Profile signal | Bottleneck |
|---|---|
| `OLAP_TABLE_SINK` time high | Write phase slow |
| `CONNECTOR_SCAN` / `FileScanNode` time high | Read phase slow |
| `OlapTableSink.RpcClientSideTime` >> `RpcServerSideTime` | Network / RPC framework |
| `OlapTableSink.RpcServerSideTime` high | Server-side storage write slow |
| `LoadChannel.WaitFlushTime` high | memtable flush pool bottleneck |
| `LoadChannel.WaitWriterTime` high | async_delta_writer pool bottleneck |
| `LoadChannel.WaitReplicaTime` high | Replica sync slow |
| `PushChunkNum` Max/Min variance large | Data skew → uneven write load |

### Step 2.3 — Route to cause

| Bottleneck found | Go to |
|---|---|
| Write slow: pool pending/execute high | Phase 3, Cause A |
| Write slow: BRPC saturated | Phase 3, Cause B |
| Write slow: pk_preload high | Phase 3, Cause C |
| Publish slow: publish queue growing | Phase 3, Cause D |
| Read slow: source side | Phase 3, Cause E |
| RPC Failed error | Phase 3, Cause F |

---

## Phase 3 — Take Action by Cause

### Cause A — Write Slow: Thread Pool Bottleneck

**Confirm all match**:
- `async_delta_writer.pending` non-zero and sustained, OR `memtable_flush.io` ≈ `execute`
- `LoadChannel.WaitFlushTime` or `WaitWriterTime` high in profile
- Cluster CPU and network are not saturated (ruling out Cause B)

**Mechanism**: Import pipeline writes through `async_delta_writer` → `memtable_flush` → disk. When the writer pool or flush pool is saturated, new tablet write tasks queue. The BRPC callback on the coordinator side waits past its deadline. If the disk (HDD) is the IO bottleneck, adding threads makes it worse — reduce concurrency instead.

```bash
# Check pool saturation
curl -s "http://<be_ip>:8060/vars" | grep "async_delta_writer"
# async_delta_writer_pending: NNN  ← non-zero = pool too small
# async_delta_writer_execute: NNN  ← high with low pending = within-task bottleneck

curl -s "http://<be_ip>:8060/vars" | grep "memtable_flush"
# memtable_flush_io: NNN  ← if ≈ execute, disk is the bottleneck
```

```sql
-- v3.2+: increase writer threads (all BEs at once)
UPDATE information_schema.be_configs SET value = 32
WHERE name = 'number_tablet_writer_threads';  -- default 16

-- Increase flush threads per disk (if IO util < 70%)
UPDATE information_schema.be_configs SET value = 4
WHERE name = 'flush_thread_num_per_store';  -- default 2 per disk

-- If HDD and IO > 90%: REDUCE threads to improve per-task throughput
UPDATE information_schema.be_configs SET value = 8
WHERE name = 'number_tablet_writer_threads';
```

```bash
# v2.5+ per-BE
curl -XPOST "http://<be_ip>:8040/api/update_config?number_tablet_writer_threads=32"
curl -XPOST "http://<be_ip>:8040/api/update_config?flush_thread_num_per_store=4"
```

Also check RocksDB (shared-nothing) if `txn_commit` sub-metric is high:

```bash
# Check rocksdb write latency
grep "Stalling writes" be/meta/LOG | tail -10
# If stalling: increase rocksdb write buffer
```

→ **Go to Phase 4 to verify recovery**

---

### Cause B — Write Slow: BRPC / Network Issue

**Confirm all match**:
- BRPC `used` ≈ `total` threads
- `Brpc Processing Requests` counter is near 0 during timeout window (server not processing)
- `netstat -na | grep 8060` shows many connections in `CLOSE_WAIT` or `TIME_WAIT`

**Mechanism**: BRPC connections between coordinator BE and executor BEs become congested or enter a broken state. New RPC calls queue behind stuck connections. The coordinator sees timeout without the executor ever processing the request. This is different from Cause A where the executor receives the call but processes it slowly.

```bash
# Check BRPC thread saturation
curl -s "http://<be_ip>:8060/vars" | grep -E "bthread_count|brpc_worker"

# Check interface-level latency
curl -s "http://<be_ip>:8060/vars" | grep -E "tablet_writer_(open|add_chunks|add_segment)"
# latency-99 high → server-side slow; latency-avg normal but P99 high → spiky

# Check TCP connection health
netstat -na | grep 8060 | awk '{print $6}' | sort | uniq -c
# Many CLOSE_WAIT or TIME_WAIT → stale connections
```

```sql
-- Increase RPC timeout
ADMIN SET FRONTEND CONFIG("brpc_send_plan_fragment_timeout_ms" = "180000");
```

```bash
# tcpdump for network-layer analysis (capture on coordinator BE)
tcpdump -i any -n port 8060 -w /tmp/brpc_$(date +%s).pcap
```

If BRPC is stuck (not recovering): restart the affected BE. Review `brpc_connection_pool_size` in FE config.

→ **Go to Phase 4 to verify recovery**

---

### Cause C — Write Slow: PK Index Rebuild

**Confirm all match**:
- `async_delta_writer.pk_preload` is high (minutes range)
- Table is a Primary Key model table
- Cluster has active clone or node decommission tasks in progress

**Mechanism**: During import, the PK model's `async_delta_writer` must preload (rebuild) the PK index for each tablet before writing. If the tablet is being cloned or decommissioned, the index is not in the local cache and must be rebuilt from scratch — this can take minutes per tablet and blocks the entire write pipeline for that tablet.

```bash
# Confirm pk_preload is the bottleneck
curl -s "http://<be_ip>:8060/vars" | grep "pk_preload"

# Check if clone tasks are running
grep "clone tablet" be.INFO | tail -20
grep "migration" fe.log | tail -20
```

```sql
-- Skip PK index preload during import (safe: index is rebuilt on next compaction)
UPDATE information_schema.be_configs SET value = "true"
WHERE name = 'skip_pk_preload';

-- Or per-BE
-- curl -XPOST "http://<be_ip>:8040/api/update_config?skip_pk_preload=true"

-- Enable persistent index to keep PK index on disk (avoids rebuild)
ALTER TABLE <pk_table_name> SET ("enable_persistent_index" = "true");
```

→ **Go to Phase 4 to verify recovery**

---

### Cause D — Publish Timeout: Compaction Lag

**Confirm all match**:
- FE `finishTransaction` log shows `publish total cost` >> `write cost`
- `starrocks_be_publish_version_queue_count` (shared-nothing) or `lake_publish_tablet_version_queuing_count` (shared-data) elevated
- Table is a Primary Key model table with high upsert rate

**Mechanism**: PK table publish uses a synchronous apply path (`enable_sync_publish`): the transaction cannot commit until all replicas have applied the new rowset — including replaying delete vectors against all accumulated rowsets. When compaction lags behind upserts, accumulated rowsets multiply apply time. Each publish blocks waiting for apply to complete. Subsequent imports queue behind pending publishes.

```bash
# Monitor publish queue depth
curl -s "http://<be_ip>:8040/metrics" | grep "publish_version_queue_count"

# Check apply latency in BE log
grep "apply_rowset_commit finish" be.INFO | tail -20
# Format: apply_rowset_commit finish. tablet=<id> cost=<ms>ms
# Cost > 5000ms per commit → severe backlog
```

```sql
-- Increase publish worker threads
-- (be.conf: transaction_publish_version_worker_count, default = #CPU cores)
UPDATE information_schema.be_configs SET value = 16
WHERE name = 'transaction_publish_version_worker_count';

-- Check PK compaction score (see compaction skill for full diagnosis)
-- High update compaction score → run manual compaction
-- curl -XPOST "http://<be_ip>:8040/api/compact?compaction_type=update&tablet_id=<tablet_id>"
```

Also check for clone tasks interfering with publish:

```bash
grep "clone.*tablet" be.INFO | tail -20
```

→ **Go to Phase 4 to verify recovery**

---

### Cause E — Read Slow: Source Side Bottleneck

**Confirm all match**:
- Profile shows `CONNECTOR_SCAN` / `FileScanNode` time >> `OLAP_TABLE_SINK` time
- OR Routine Load task count equals Kafka partition count and lag grows monotonically

**Mechanism**: The read phase (source → StarRocks) is the bottleneck. For Routine Load, parallelism is capped by Kafka partition count — adding StarRocks resources provides no relief. For Broker Load, many small files cause excessive open/seek overhead. For Stream Load with large JSON batches, deserialization is the CPU bottleneck.

| Import Type | Common Causes | Solutions |
|---|---|---|
| Stream Load | HTTP client to StarRocks network slow; JSON format with large batches | Reduce batch size; try CSV format |
| Routine Load | Small batch size; too few Kafka partitions | Increase `max_routine_load_batch_size` and `routine_load_task_consume_second`; add Kafka partitions |
| Broker Load | Many small files; slow file storage | Consolidate files; check storage performance |
| INSERT INTO | Complex SELECT query | Optimize query |

```sql
-- Routine Load: increase batch size and consumption window
ALTER ROUTINE LOAD FOR <job_name>
PROPERTIES (
  "max_routine_load_batch_size" = "209715200",   -- 200MB (default 100MB)
  "routine_load_task_consume_second" = "30"       -- default 15s
);

-- Check Routine Load parallelism vs Kafka partition count
SHOW ROUTINE LOAD FOR <job_name>\G
-- TaskRunning == Kafka partition count → cannot increase parallelism without repartitioning
```

For Flink connector: check Flink CPU utilization first — it is often the bottleneck, not StarRocks.

- `sink.buffer-flush.max-bytes`
- `sink.buffer-flush.max-rows`
- `sink.buffer-flush.interval-ms`
- `checkpoint-interval`

→ **Go to Phase 4 to verify recovery**

---

### Cause F — RPC Failed: Statistics Collection Conflict

**Confirm all match**:
- Import `ERROR_MSG` contains `RPC Failed`
- Failure pattern is intermittent, correlated with scheduled statistics collection windows
- FE log shows many concurrent ANALYZE tasks in `information_schema.task_runs`

**Mechanism**: Automatic ANALYZE triggers large-scale statistics scans across many tables. These scans saturate the BRPC send queue on BEs. Incoming plan fragment delivery for import tasks fails because the BRPC worker queue is full. FE retries exhaust or time out and the import transaction aborts.

```bash
# Check brpc latency during the failure window
curl -s "http://<be_ip>:8060/vars" | grep exec_

# Check TCP connection state on BRPC port
netstat -na | grep 8060 | head -30
```

```sql
-- Immediate mitigation: disable automatic statistics collection
ADMIN SET FRONTEND CONFIG("enable_collect_full_statistic" = "false");
ADMIN SET FRONTEND CONFIG("enable_statistic_collect" = "false");
ADMIN SET FRONTEND CONFIG("enable_statistic_collect_on_first_load" = "false");

-- Increase RPC timeout to tolerate BRPC congestion
ADMIN SET FRONTEND CONFIG("brpc_send_plan_fragment_timeout_ms" = "180000");

-- Long-term: tune statistics collection schedule
-- Increase interval and reduce concurrency
ADMIN SET FRONTEND CONFIG("statistic_collect_interval_sec" = "1200");
ADMIN SET FRONTEND CONFIG("statistic_collect_concurrency" = "1");
```

→ **Go to Phase 4 to verify recovery**

---

## Phase 4 — Verify Recovery

```sql
-- Confirm import tasks are completing successfully
SELECT STATE, COUNT(*) AS cnt, AVG(TIMESTAMPDIFF(SECOND, LOAD_START_TIME, LOAD_FINISH_TIME)) AS avg_sec
FROM information_schema.loads
WHERE LOAD_FINISH_TIME > DATE_SUB(NOW(), INTERVAL 10 MINUTE)
GROUP BY STATE;
-- FINISHED should dominate; CANCELLED/FAILED should be 0

-- Confirm no new import failures
SELECT LABEL, STATE, ERROR_MSG
FROM information_schema.loads
WHERE STATE = 'CANCELLED'
ORDER BY LOAD_FINISH_TIME DESC
LIMIT 10;

-- Confirm publish queue is draining (PK tables)
-- Monitor over 2-3 minutes: count should decrease
SELECT COUNT(*) AS pending_publish
FROM information_schema.loads
WHERE STATE = 'COMMITED';
```

```bash
# Confirm thread pool metrics are back to normal
curl -s "http://<be_ip>:8060/vars" | grep "async_delta_writer_pending"
# Should be 0 or near-0

curl -s "http://<be_ip>:8060/vars" | grep "memtable_flush_io"
# Should be proportional to actual import load (not 100%)

# Confirm no new timeout errors in BE log
grep "Reached timeout\|RPC Failed" be.WARNING | tail -10
# Should be empty or timestamps should be in the past
```

Grafana: `starrocks_be_publish_version_queue_count` should return to near-0. Import throughput (`sink_rows` per minute) should return to baseline.

---

## Thread Pool Configuration Reference

### Shared-Nothing Write Pipeline

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `number_tablet_writer_threads` | 16 | async_delta_writer pool size | Yes (v2.5+) |
| `flush_thread_num_per_store` | 2 per disk | memtable_flush / segment_replicate_sync / segment_flush pool size | Yes (v2.5+) |
| `brpc_num_threads` | #CPU cores | BRPC worker thread count | No (restart) |
| `write_buffer_size` | 100MB | Memtable size before flush trigger | Yes (v2.5+) |
| `transaction_publish_version_worker_count` | #CPU cores | Publish version worker threads | No (restart) |
| `load_process_max_memory_limit_bytes` | 100G | Upper bound for import memory | No (restart) |
| `load_process_max_memory_limit_percent` | 30% | Actual limit = `mem_limit * 90% * 30%` | No (restart) |
| `skip_pk_preload` | false | Skip PK index rebuild during import | Yes (v2.5+) |
| `async_load_task_pool_size` | 10 | Broker Load loading thread pool | No (restart) |

### Shared-Data Write Pipeline

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `lake_flush_thread_num_per_store` | 2 × #CPU | memtable flush pool for shared-data | Yes (v2.5+) |

### Applying Dynamic Parameters

```sql
-- v3.2+: apply to all BEs at once
UPDATE information_schema.be_configs SET value = <value> WHERE name = '<param>';
SELECT * FROM information_schema.be_configs WHERE name = '<param>';  -- verify
```

```bash
# v2.5+: apply per BE
curl -XPOST "http://<be_ip>:<be_http_port>/api/update_config?<param>=<value>"
curl "http://<be_ip>:<be_http_port>/varz" | grep <param>  # verify
```

Static parameters must be set in `be.conf` and require restart.

---

## Import Observation Reference

### Show Commands

| Command | Purpose |
|---|---|
| `SHOW ROUTINE LOAD` | View Routine Load job status |
| `SHOW PIPES` | View Pipe job status |
| `SHOW LOAD` | View Broker Load/Insert Into/Spark Load tasks (running + recently completed) |
| `SHOW ROUTINE LOAD TASK` | View Routine Load tasks |

### information_schema.loads — Full Schema

| Field | Description |
|---|---|
| ID | Global unique ID |
| LABEL | Import job label |
| PROFILE_ID | Profile ID for `ANALYZE PROFILE FROM 'profile_id'` (NULL if no profile) |
| DB_NAME / TABLE_NAME | Target database/table |
| USER / WAREHOUSE | Initiating user / warehouse |
| STATE | PENDING, BEGIN, QUEUEING, BEFORE_LOAD, LOADING, PREPARING, PREPARED, COMMITED, FINISHED, CANCELLED |
| PROGRESS | ETL and LOADING phase progress |
| TYPE | BROKER, INSERT, ROUTINE, STREAM |
| PRIORITY | HIGHEST, HIGH, NORMAL, LOW, LOWEST |
| SCAN_ROWS / SCAN_BYTES | Total scanned rows/bytes |
| FILTERED_ROWS / UNSELECTED_ROWS | Quality-filtered / WHERE-filtered rows |
| SINK_ROWS | Successfully imported rows |
| CREATE_TIME / LOAD_START_TIME / LOAD_COMMIT_TIME / LOAD_FINISH_TIME | Timestamps |
| ERROR_MSG | Error message (NULL if none) |
| TRACKING_SQL | Query for rejected records |
| REJECTED_RECORD_PATH | Path to quality-rejected records |

---

## Common Issues Quick Reference

| Issue | Cause | Fix |
|---|---|---|
| Broker Load tasks queue forever | `async_load_task_pool_size` too small or stuck tasks | Increase pool size; trace stuck task by label |
| `Reached timeout` on Stream Load | brpc / async_delta_writer pool stuck | Restart BE; tune brpc threads |
| `publish timeout` on PK table | Compaction lagging or PK index rebuild | Tune compaction; `skip_pk_preload = true` |
| RPC Failed during nightly batch | Statistics collection saturating brpc | Disable full statistics; reduce concurrency |
| Routine Load not keeping up | Small batch, few Kafka partitions | Increase batch size; add Kafka partitions |

---

## Related Cases

- `case-001-broker-load-backlog` — task pool saturation root cause
- `case-002-rpc-failed-statistics` — statistics collection starving RPC
- `case-007-memory-tracking-leak` — slow imports caused by memory tracker leak
- `case-009-stream-load-stuck` — tablet meta cache bug

---

## Causal Chains

### Chain 1: async_delta_writer Pool Saturation → Write Timeout

```
High-concurrency imports saturate async_delta_writer thread pool
  ↓ observable: BE pool metrics show active ≈ total with queue > 0 for async_delta_writer
New delta write tasks queue; flush cannot proceed
  ↓ observable: BE log shows wait_flush duration growing per tablet write
BRPC callback exceeds deadline waiting for write acknowledgement
  ↓ observable: BE log "Reached timeout" on tablet write RPC; import task STATE stuck in LOADING
FE times out waiting for tablet commit
```
Import job fails with `Reached timeout`; data not committed.

**Trigger conditions**: `write_buffer_size` too small causing excessive flush frequency; import concurrency exceeds thread pool capacity.

**Break point**: Increase `number_tablet_writer_threads`; raise `write_buffer_size`; reduce import concurrency at source.

---

### Chain 2: Statistics Collection Saturates BRPC → RPC Failed

```
Automatic ANALYZE triggers large-scale statistics scan across many tables
  ↓ observable: FE log shows many concurrent ANALYZE tasks in information_schema.task_runs
BRPC send queue on BE fills; plan fragment delivery fails
  ↓ observable: BE log "plan fragment send fail"; FE log "RPC Failed" sending fragments to BE
Import fragment cannot reach BE; FE retries exhaust or time out
  ↓ observable: information_schema.loads shows STATE = FAILED with error containing "RPC Failed"
Import transactions abort
```
Import jobs fail intermittently during scheduled statistics collection windows.

**Trigger conditions**: Statistics collection scheduled during peak import hours; `brpc_worker_threads` under-provisioned.

**Break point**: Separate statistics schedule from peak import windows; reduce `statistic_collect_concurrency`; disable via `ADMIN SET FRONTEND CONFIG("enable_collect_full_statistic" = "false")`.

---

### Chain 3: PK Table Compaction Lag → Rowset Accumulation → Publish Timeout

```
High-frequency upserts on PK table; compaction cannot keep pace
  ↓ observable: starrocks_be_publish_version_queue_count elevated and growing; num_rowset per tablet rising
Apply thread must replay many delete vectors against accumulated rowsets
  ↓ observable: BE log shows pk_preload latency increasing; wait_replica extends per commit
Publish version step blocks waiting for apply to complete
  ↓ observable: FE finishTransaction log shows publish cost >> write cost
Transaction publish deadline exceeded
```
Import fails with `publish timeout`; subsequent imports on the same table also stall.

**Trigger conditions**: PK table with high upsert rate; `pk_index_cache_capacity` too small; `max_pk_compaction_threads` not scaled.

**Break point**: Increase `max_pk_compaction_threads`; enlarge `pk_index_cache_capacity`; monitor `starrocks_be_publish_version_queue_count` as leading indicator.

---

### Chain 4: Memory Tracker Leak → Import Throttle → Progress Frozen

```
Long-running BE process accumulates unreleased memory in tracker
  ↓ observable: SHOW PROC '/current_queries' shows cumulative memUsageBytes not decreasing after query completion
Memory tracker reports usage near mem_limit; import admission control activates
  ↓ observable: BE log shows import tasks throttled or rejected with "memory exceed"
Import tasks queue but cannot start; progress metric frozen
  ↓ observable: information_schema.loads shows STATE = LOADING with PROGRESS barely advancing over minutes
Import times out or is manually cancelled
```
Import appears to start successfully but makes no progress; restarting BE temporarily resolves until leak accumulates again.

**Trigger conditions**: Memory tracker reference-count bug in a specific code path; BE running for days without restart.

**Break point**: Audit `SHOW PROC '/current_queries'` memory; rolling restart affected BE; patch tracker release on error exit paths.

---

### Chain 5: Routine Load Kafka Partition Bottleneck → Lag Grows

```
Routine Load task count capped by Kafka partition count
  ↓ observable: SHOW ROUTINE LOAD shows TaskRunning == Kafka partition count; cannot increase parallelism
Incoming message rate exceeds drain rate; lag grows
  ↓ observable: Kafka consumer group lag metric grows monotonically; PROGRESS in information_schema.loads stalls
Unconsumed messages accumulate in Kafka topic
  ↓ observable: kafka-consumer-groups.sh --describe shows LAG increasing per partition
Routine Load lag grows unboundedly; data freshness SLA breached
```
Downstream queries see stale data; eventually Kafka retention boundary reached and messages dropped.

**Trigger conditions**: Topic created with too few partitions; data volume grew after initial sizing.

**Break point**: Increase Kafka topic partition count; raise `desired_concurrent_number` in Routine Load job after repartitioning.

---

## Cross-Skill Guides

- [`guides/cascade-import-rpc-failed.md`](../guides/cascade-import-rpc-failed.md) — Statistics collection → BRPC starvation → import/query RPC failure. Read this when imports fail with "RPC Failed" and ANALYZE tasks are running concurrently.
- [`guides/cascade-cluster-degradation.md`](../guides/cascade-cluster-degradation.md) — Full cluster degradation cascade ending in BE OOM and FE deadlock; import is a key stage in the chain.

---

## Resources

- [Loading Troubleshooting](https://docs.starrocks.io/docs/loading/loading_introduction/troubleshooting_loading/)
- [Query Profile Structure](https://docs.starrocks.io/docs/administration/query_profile/)
