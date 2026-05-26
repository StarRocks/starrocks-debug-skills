---
name: 07-tablet
description: "Use when investigating tablet-level structural problems: data skew (one tablet 10× larger than peers), too many tablet versions blocking writes (too many versions error), error-state replicas, over-sharding causing high FE metadata overhead, or disk balance retry loops. Covers SHOW TABLET analysis, be_tablets SQL queries, bucket key optimization, and tablet health thresholds (VersionCount, DataSize variance, num_rowset)."
version: 2.0.0
category: tablet
keywords:
- tablet health
- tablet balance
- data skew
- bucket
- compaction
- version count
tools: []
related_cases:
- case-004-disk-balancing
- case-014-scan-skew
---

# 07 - Tablet Governance

Investigation guide for tablet health, balance, data skew, bucket optimization, and
version-count / compaction-lag issues.

Five root causes account for the vast majority of cases:

- **Cause A** — data skew from low-cardinality bucket key
- **Cause B** — too many versions (compaction lag)
- **Cause C** — error state replicas (VERSION_ERROR, SCHEMA_ERROR)
- **Cause D** — too many tablets (over-sharding from fine partitions or excess buckets)
- **Cause E** — disk balance retry loop (metadata state inconsistency)

---

## Metric Taxonomy — Read This First

Before using any metrics, understand the two-layer observability structure:

### SQL-based observability

**`SHOW TABLET FROM <table>`** — per-tablet summary for a table:

| Field | Meaning |
|---|---|
| `TabletId` | Tablet identifier |
| `ReplicaId` | Replica identifier on a specific BE |
| `BackendId` | BE hosting this replica |
| `SchemaHash` | Schema version hash (mismatch indicates SCHEMA_ERROR) |
| `Version` | Latest visible version |
| `VersionCount` | Number of versions (rowsets) tracked by the system |
| `LstFailedVersion` | Last version that failed to load — non-zero indicates replica problem |
| `LstSuccessVersion` | Last version successfully applied |
| `DataSize` | Storage size of this replica in bytes |
| `RowCount` | Row count estimate |
| `State` | Replica state: NORMAL, CLONE, SCHEMA_CHANGE, ALTER |

**`SHOW TABLET <tablet_id>`** — single tablet detail:

| Field | Meaning |
|---|---|
| `DbName / TableName / PartitionName` | Context for this tablet |
| `State` | Tablet state |
| `VersionCount` | Version count across replicas (from `detailcmd`) |
| `CompactionStatus` URL | Link to BE compaction state JSON — shows `last_base_compaction_time`, `cumulative_layer_point` |

**`information_schema.be_tablets`** — per-replica metrics from BE (most detailed):

| Field | Meaning |
|---|---|
| `be_id` | BE hosting this replica |
| `tablet_id` | Tablet identifier |
| `num_version` | Total version count (all edits ever) |
| `num_rowset` | Active rowsets in current version — key compaction health indicator |
| `num_segment` | Segment files (num_rowset × segments per rowset) |
| `data_size` | Raw data size in bytes |
| `num_row` | Row count |
| `state` | Replica state |
| `medium_type` | HDD or SSD |

### How to retrieve metrics

**Option 1 — SQL (primary method)**

```sql
-- Per-table tablet distribution and health
SHOW TABLET FROM <table_name>;

-- Single tablet detail + detailcmd for replica-level VersionCount
SHOW TABLET <tablet_id>;
-- Run the detailcmd from output:
SHOW PROC '/dbs/<db_id>/<table_id>/<partition_id>';

-- Tablet replica distribution across BEs
ADMIN SHOW REPLICA DISTRIBUTION FROM <table_name>;

-- Deep tablet state per BE (most detailed)
SELECT be_id, tablet_id, num_rowset, num_version, num_segment,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb,
       num_row, state
FROM information_schema.be_tablets
WHERE table_id = <table_id>
ORDER BY num_rowset DESC LIMIT 20;

-- Cluster-wide unhealthy tablets
SHOW PROC "/statistic";
SHOW PROC "/statistic/<db_id>";

-- Find tablets with high rowset count (compaction lag indicator)
SELECT be_id, tablet_id, num_rowset, num_segment,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb
FROM information_schema.be_tablets
WHERE num_rowset > 100
ORDER BY num_rowset DESC LIMIT 20;

-- Find data skew: tablets with extreme size variance
SELECT be_id, tablet_id,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb,
       num_row
FROM information_schema.be_tablets
WHERE table_id = <table_id>
ORDER BY data_size DESC LIMIT 20;

-- Count tablets per BE (distribution balance check)
SELECT be_id, COUNT(*) AS tablet_count,
       SUM(data_size) / 1024 / 1024 / 1024 AS total_gb
FROM information_schema.be_tablets
GROUP BY be_id
ORDER BY tablet_count DESC;
```

**Option 2 — curl BE HTTP port (compaction and rowset state)**

```bash
# Compaction status for a specific tablet
curl "http://<be_ip>:8040/api/compaction/show?tablet_id=<tablet_id>"

# All compaction metrics on a BE
curl -s "http://<be_ip>:8040/metrics" | grep -E "compaction_score|num_rowset"
```

**Option 3 — BE HTTP tablet detail**

```bash
# Full tablet metadata from BE
curl "http://<be_ip>:8040/api/meta/header?tablet_id=<tablet_id>"
```

**Default HTTP ports**: BE = 8040, FE = 8030.

---

**Key rules for interpretation**:

1. `VersionCount > 500` continuously (from `SHOW TABLET`) indicates compaction cannot keep up with write rate — this is a compaction problem (see skill 12-compaction), not a tablet governance problem.
2. `DataSize` variance > 10x across tablets of the same table strongly indicates data skew from a low-cardinality or hot-value bucket key.
3. `num_rowset` in `be_tablets` equals `VersionCount` for non-PK tables; both come from `_rs_metas.size()`. For PK tables, `num_rowset` = active rowsets in latest version (usually smaller).
4. `LstFailedVersion > 0` and `LstFailedVersion != LstSuccessVersion` on a replica means that replica has a version gap — it needs repair clone.
5. Total tablet count per cluster should be monitored: > 500,000 tablets creates FE metadata overhead and BE file descriptor pressure.

---

## Tablet Health Reference

| Metric | Healthy | Warning | Act Immediately |
|---|---|---|---|
| `VersionCount` (SHOW TABLET) | < 50 | 50–500 | > 500 sustained (compaction lag) |
| `DataSize` variance across tablets (same table) | < 2x | 2–10x | > 10x (data skew) |
| `num_rowset` (be_tablets) | < 20 | 20–100 | > 100 (rowset pile-up) |
| `num_row` std deviation across tablets | small | std_dev > 10% of mean | single tablet holds > 50% of total rows |
| Tablet count per BE | balanced | 20% variance | > 2x between BEs (scan skew) |
| Total tablet count in cluster | < 100,000 | 100,000–500,000 | > 500,000 (FE heap and FD pressure) |
| `LstFailedVersion` on replica | 0 or equals LstSuccessVersion | Any non-zero gap | Gap growing and CloningTabletNum not rising |

---

## Phase 1 — Assess Tablet Health

### Step 1.1 — Cluster-wide quick scan

```sql
-- Check for unhealthy tablets across all databases
SHOW PROC "/statistic";
```

- `UnhealthyTabletNum = 0` everywhere → proceed to performance-focused steps
- `UnhealthyTabletNum > 0` → note which DB IDs are affected, go to Step 1.2

### Step 1.2 — BE-level tablet scan

```sql
-- Find tablets with high rowset count (compaction lag signal)
SELECT be_id, COUNT(*) AS tablet_count,
       MAX(num_rowset) AS max_rowsets,
       SUM(CASE WHEN num_rowset > 100 THEN 1 ELSE 0 END) AS tablets_over_100_rowsets,
       SUM(CASE WHEN num_rowset > 500 THEN 1 ELSE 0 END) AS tablets_over_500_rowsets
FROM information_schema.be_tablets
GROUP BY be_id
ORDER BY max_rowsets DESC;

-- Check for severely skewed tablets across all tables
SELECT table_id, be_id,
       MAX(data_size) / 1024 / 1024 AS max_tablet_mb,
       MIN(data_size) / 1024 / 1024 AS min_tablet_mb,
       MAX(data_size) / NULLIF(MIN(data_size), 0) AS size_ratio
FROM information_schema.be_tablets
GROUP BY table_id, be_id
HAVING size_ratio > 10
ORDER BY size_ratio DESC LIMIT 10;
```

### Step 1.3 — Identify issue type

| Signal from Step 1.2 | Points toward |
|---|---|
| `max_rowsets > 100` on multiple BEs | Cause B — compaction lag |
| `size_ratio > 10` in data skew query | Cause A — bucket key skew |
| `UnhealthyTabletNum > 0`, `ErrorStateTabletNum > 0` | Cause C — error state replicas |
| Total tablet count > 500,000 | Cause D — over-sharding |
| `history_tablets` shows balance tasks cycling | Cause E — disk balance retry loop |

---

## Phase 2 — Identify Problem Tablets

### Step 2.1 — Drill into suspect table

```sql
-- Tablet distribution for the table
SHOW TABLET FROM <table_name>;

-- Replica distribution across BEs
ADMIN SHOW REPLICA DISTRIBUTION FROM <table_name>;

-- Detailed per-replica metrics
SELECT be_id, tablet_id, num_rowset, num_version,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb,
       num_row, state
FROM information_schema.be_tablets
WHERE table_id = <table_id>
ORDER BY data_size DESC LIMIT 30;
```

### Step 2.2 — Confirm root cause

#### Cause A — Data skew

**Confirm all match**:
- `DataSize` varies > 10x between tablets of the same table
- `num_row` also varies proportionally (not a compaction artifact)
- Distribution key has low cardinality or a dominant hot value

→ **Go to Phase 3, Cause A**

#### Cause B — Too many versions

**Confirm all match**:
- `num_rowset > 100` in `be_tablets` for multiple tablets
- `VersionCount > 500` from `SHOW TABLET <tablet_id>`
- Compaction score rising (check skill 12-compaction for compaction-specific diagnosis)

→ **Go to Phase 3, Cause B**

#### Cause C — Error state replicas

**Confirm all match**:
- `ErrorStateTabletNum > 0` in `SHOW PROC "/statistic"`
- `SHOW TABLET FROM <table>` shows `LstFailedVersion > 0` and differs from `LstSuccessVersion`
- Replica `State` is not NORMAL

→ **Go to Phase 3, Cause C**

#### Cause D — Too many tablets

**Confirm all match**:
- Total tablet count > 500,000 or tablet count per BE > 50,000
- Many small partitions (< 100MB each) or excess bucket count relative to data size
- BE log shows FD warnings or `ulimit -n` approaching limit

→ **Go to Phase 3, Cause D**

#### Cause E — Disk balance retry loop

**Confirm all match**:
- `SHOW PROC "/cluster_balance/history_tablets"` shows same tablet repeatedly failing
- `ErrMsg` references metadata state or `_shutdown_tablets`
- BE IO elevated on specific disk without progress

→ **Go to Phase 3, Cause E**

---

## Phase 3 — Take Action by Cause

### Cause A — Data skew (low-cardinality bucket key)

**Step A-1: Confirm skew pattern**

```sql
-- Check approximate value distribution in the bucket key column
SELECT <bucket_key_col>, COUNT(*) AS cnt
FROM <table_name>
GROUP BY <bucket_key_col>
ORDER BY cnt DESC LIMIT 20;
-- If top value holds >> 1/bucket_count of total rows → bucket key has hot values
```

**Step A-2: Identify correct bucket key**

The bucket key must have high cardinality and uniform distribution. Good candidates:
- User ID, order ID, device ID (UUID-like)
- Composite keys that spread data evenly

Avoid: `status`, `region`, `date`, `boolean` columns.

**Step A-3: Recreate table with better bucket key**

```sql
-- Create new table with better bucket key
CREATE TABLE <table_name>_new
DISTRIBUTED BY HASH(<high_cardinality_col>) BUCKETS <n>
AS SELECT * FROM <table_name>;

-- Swap
ALTER TABLE <table_name> RENAME <table_name>_old;
ALTER TABLE <table_name>_new RENAME <table_name>;
```

**Step A-4: Use StarRocksBuckets CLI for analysis**

The standalone `StarRocksBuckets` utility can analyze existing distributions and generate
`ALTER TABLE` DDL for optimal bucket count and key. Run against the affected table before
deciding on the new schema.

---

### Cause B — Too many versions (compaction lag)

**Step B-1: Identify affected tablets**

```sql
SELECT be_id, tablet_id, num_rowset,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb
FROM information_schema.be_tablets
WHERE num_rowset > 100
ORDER BY num_rowset DESC LIMIT 20;
```

**Step B-2: Confirm compaction type and take action**

This is a compaction problem. Use skill **12-compaction** for the full diagnosis.

Quick remediation:

```bash
# Manual compaction trigger (cumulative)
curl -XPOST "http://<be_ip>:8040/api/compact?compaction_type=cumulative&tablet_id=<tablet_id>"

# Manual compaction trigger (base — for delete explosion)
curl -XPOST "http://<be_ip>:8040/api/compact?compaction_type=base&tablet_id=<tablet_id>"
```

```sql
-- Increase compaction throughput (v3.2+)
UPDATE information_schema.be_configs SET value = 8
WHERE name = 'max_compaction_concurrency';

UPDATE information_schema.be_configs SET value = 2
WHERE name = 'cumulative_compaction_check_interval_seconds';
```

**Step B-3: Reduce import frequency if needed**

```sql
-- Routine Load: batch more rows per interval
ALTER ROUTINE LOAD FOR <job_name>
PROPERTIES (
  "max_batch_interval" = "60",
  "desired_concurrent_number" = "3"
);
```

---

### Cause C — Error state replicas

**Step C-1: Find affected replicas**

```sql
-- Check for version gaps across replicas of a specific tablet
SHOW TABLET <tablet_id>;
-- Run the detailcmd from output
-- Compare Version and LstFailedVersion across replicas
```

**Step C-2: Trigger repair clone**

```sql
-- Mark bad replica to trigger re-replication from healthy replica
ADMIN SET REPLICA STATUS PROPERTIES(
  "tablet_id" = "<tablet_id>",
  "backend_id" = "<bad_be_id>",
  "status" = "bad"
);
```

**Step C-3: Force priority repair**

```sql
ADMIN REPAIR TABLE <table_name>;
ADMIN REPAIR TABLE <table_name> PARTITION (<partition_name>);
```

**Step C-4: Verify**

```sql
-- After repair clone completes, LstFailedVersion should equal LstSuccessVersion
SHOW TABLET <tablet_id>;
```

---

### Cause D — Too many tablets (over-sharding)

**Step D-1: Quantify the problem**

```sql
-- Total tablet count in cluster
SELECT COUNT(*) AS total_tablets FROM information_schema.be_tablets;

-- Tablets per BE
SELECT be_id, COUNT(*) AS tablet_count
FROM information_schema.be_tablets
GROUP BY be_id ORDER BY tablet_count DESC;

-- Find tables with most tablets
SELECT table_id, COUNT(DISTINCT tablet_id) AS tablet_count
FROM information_schema.be_tablets
GROUP BY table_id
ORDER BY tablet_count DESC LIMIT 20;
```

**Step D-2: Check file descriptor pressure**

```bash
# Find BE PID
ps aux | grep starrocks_be

# Check current FD usage
ls /proc/<be_pid>/fd | wc -l

# Check FD limit
cat /proc/<be_pid>/limits | grep "open files"
```

**Step D-3: Increase FD limit (immediate relief)**

```bash
# In BE startup script or systemd unit
ulimit -n 1000000

# Or in /etc/security/limits.conf
*  soft  nofile  1000000
*  hard  nofile  1000000
```

**Step D-4: Reduce tablet count structurally**

```sql
-- Merge small partitions (use dynamic partitioning at coarser granularity)
-- Example: change daily partitions to monthly
ALTER TABLE <table_name>
SET ("dynamic_partition.time_unit" = "MONTH");

-- Reduce bucket count on over-sharded tables
-- Target: ~1GB per tablet (raw data)
CREATE TABLE <table_name>_new
DISTRIBUTED BY HASH(<col>) BUCKETS <lower_n>
AS SELECT * FROM <table_name>;

-- Drop unused test/backup tables
DROP TABLE <old_table_name>;

-- Implement partition TTL to auto-expire old data
ALTER TABLE <table_name>
SET ("dynamic_partition.history_partition_num" = "90");
```

---

### Cause E — Disk balance retry loop

**Step E-1: Confirm the loop**

```sql
-- Check for same tablet repeatedly failing in balance history
SHOW PROC "/cluster_balance/history_tablets";
-- Look for same TabletId appearing many times with FAILED status
```

**Step E-2: Restart all BE nodes (resets `_shutdown_tablets` state)**

```bash
# Rolling restart: restart BEs one at a time
# Wait for each to rejoin before restarting the next
./bin/stop_be.sh && sleep 5 && ./bin/start_be.sh
```

After restart, `_shutdown_tablets` state is cleared and the balance task can proceed cleanly.

**Step E-3: Monitor recovery**

```sql
-- Confirm balance tasks completing without repeated failures
SHOW PROC "/cluster_balance/history_tablets";
SHOW PROC "/statistic";
```

---

## Phase 4 — Verify Recovery

```sql
-- Primary signal: all tablets healthy
SHOW PROC "/statistic";
-- UnhealthyTabletNum = 0, ErrorStateTabletNum = 0

-- Confirm skew resolved (Cause A)
ADMIN SHOW REPLICA DISTRIBUTION FROM <table_name>;
-- TabletCount should be balanced; RowCount distribution should be even

-- Confirm compaction recovering (Cause B)
SELECT be_id, MAX(num_rowset) AS max_rowsets,
       SUM(CASE WHEN num_rowset > 100 THEN 1 ELSE 0 END) AS tablets_over_100
FROM information_schema.be_tablets
WHERE table_id = <table_id>
GROUP BY be_id;
-- num_rowset should be decreasing

-- Confirm replica health (Cause C)
SHOW TABLET <tablet_id>;
-- LstFailedVersion should equal LstSuccessVersion across all replicas

-- Confirm tablet count reduced (Cause D)
SELECT COUNT(*) AS total_tablets FROM information_schema.be_tablets;
```

---

## Tablet Governance Config Parameters

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `tablet_max_versions` | 1000 | Max versions before write block ("too many versions") | Yes (v2.3+) |
| `cumulative_compaction_num_threads_per_disk` | 1 | Cumulative compaction threads per disk | No (restart) |
| `base_compaction_num_threads_per_disk` | 1 | Base compaction threads per disk | No (restart) |
| `max_compaction_concurrency` | -1 (unlimited) | Global compaction thread cap | Yes (v2.5+) |
| `cumulative_compaction_check_interval_seconds` | 1 | Cumulative compaction scheduler poll interval | Yes (v2.5+) |
| `base_compaction_check_interval_seconds` | 60 | Base compaction scheduler poll interval | Yes (v2.5+) |
| `base_cumulative_delta_ratio` | 0.3 | Cumulative/base size ratio to trigger base compaction | Yes (v2.5+) |
| `base_compaction_interval_seconds_since_last_operation` | 86400 | Periodic base compaction timer | Yes (v2.5+) |
| `parallel_clone_task_per_path` | 8 | Clone threads per disk path (for balance repair) | No (restart) |
| `storage_page_cache_limit` | system-dependent | Block cache size; affects tablet read performance | No (restart) |
| `max_tablet_num_per_shard` | 1024 | Max tablets per shard dir; tune for high tablet count | No (restart) |

**Session-level** (affects queries on tables, not stored in be.conf):

| Session Variable | Default | Description |
|---|---|---|
| `enable_profile` | false | Enable query profiling (to inspect scan skew) |
| `pipeline_dop` | 0 (auto) | Query parallelism; affects per-tablet scan concurrency |

---

## Common Issues

| Issue | Cause | Fix |
|---|---|---|
| `too many tablet versions` | Compaction can't keep up with ingest | Tune compaction; reduce import frequency (see skill 12-compaction) |
| Single tablet 100x larger than others | Bad bucket key (low cardinality / hot value) | Re-bucket with high-cardinality key (Cause A) |
| Disk balance retries forever | `_shutdown_tablets` state inconsistent | Restart all BEs (Cause E) |
| `VERSION_ERROR` on a replica | Replica desync after network blip | Mark replica bad to trigger clone (Cause C) |
| Tablet count too high | Too-fine partition or excess buckets | Coarser partition; reduce bucket count (Cause D) |
| Queries slow on specific table | Scan skew from tablet size imbalance | Check DataSize distribution; fix bucket key |

---

## Related Cases

- `case-004-disk-balancing` — IO saturation from migration retry loop
- `case-014-scan-skew` — data skew driven by bucket key with hot value

---

## Causal Chains

### Chain 1: Low-Cardinality Bucket Key → Data Skew → Single Tablet Straggler

```
Table created with low-cardinality distribution key
  ↓ observable: SHOW TABLET FROM <table> shows DataSize variance > 10x across tablets
All data for dominant key values routes to one or few tablets
  ↓ observable: information_schema.partitions shows uneven DATA_LENGTH; SHOW PROC '/cluster_balance/tablet_checker' may show oversized tablets
Query scan assigns one instance to the large tablet; parallelism nominally full
  ↓ observable: Profile per-instance Active time in OLAP_SCAN_NODE shows one instance orders of magnitude slower
Query bottlenecked on single slow scan instance; others idle-wait
  ↓ observable: max instance active time >> avg; total query time equals slowest instance
```
Slow queries despite sufficient cluster resources and parallelism.

**Trigger conditions**: Distribution key chosen by convenience (status, region) not cardinality; business-domain skewed data.

**Break point**: Recreate table with high-cardinality distribution key or composite bucket key; increase bucket count.

---

### Chain 2: Inverted Index Pending Tasks → Version Count Rises → Compaction Lag → Scan Slow

```
Inverted index build tasks accumulate due to high write rate or slow index worker
  ↓ observable: BE log inverted index pending task queue depth increasing; index build lag metric rising
Tablet cannot compact rowsets with pending index tasks; version count rises
  ↓ observable: SELECT num_rowset FROM information_schema.be_tablets shows affected tablets with high counts; BE log compaction skipped due to pending index
Compaction falls behind; many unmerged rowsets accumulate
  ↓ observable: Grafana compaction score rising; BE log compaction score warnings
Scan must MERGE many rowsets at query time
  ↓ observable: Profile MERGE time dominant in OLAP_SCAN_NODE; inverted index scan time elevated
```
Slow queries on tables with inverted indexes under high write load.

**Trigger conditions**: Inverted index on high-cardinality columns with frequent updates; index worker thread count insufficient.

**Break point**: Increase inverted index build thread count; temporarily reduce import frequency.

---

### Chain 3: Tablet Count Too High Per BE → FD Limit Hit → Write Fails

```
Table/partition proliferation results in too many tablets per BE
  ↓ observable: SHOW PROC '/cluster_balance/tablet_checker' shows high tablet count per BE; BE log tablet count warning
Each tablet holds open file descriptors for segment, bloom filter, and index files
  ↓ observable: ls /proc/<be_pid>/fd | wc -l approaches ulimit -n value
BE reaches OS fd limit; cannot open new segment files for writes or reads
  ↓ observable: BE log "Too many open files"; ulimit -n shows limit lower than actual FD usage
New segment writes fail; load jobs return IO error from affected BE
  ↓ observable: Load job error: file open failed; TabletCount in Profile very high
```
Write failures on BEs with extreme tablet counts.

**Trigger conditions**: Many small partitions without TTL; high bucket count tables; FD limit not tuned beyond OS default (65535).

**Break point**: Increase `ulimit -n` for BE process (typically to 1000000); implement partition TTL; reduce bucket count on over-sharded tables.

---

## Resources

- [Tablet management documentation](https://docs.starrocks.io/docs/administration/management/tablet_management/)
