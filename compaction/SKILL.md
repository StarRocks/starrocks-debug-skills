---
name: 12-compaction
description: "Use when compaction score is sustained above 100, imports are failing with 'too many versions', or query MERGE time is elevated. Covers three root causes: import rate exceeding cumulative compaction throughput (Cause A), batch DELETE creating delete-predicate version explosion (Cause B), and large tablet where base compaction is not self-triggering (Cause C). Shared-nothing deployments only — for shared-data see shared-data/SKILL.md."
version: 3.0.0
category: compaction
keywords:
- compaction
- too many versions
- compaction score
- base compaction
- cumulative compaction
- size-tiered compaction
- rowset
- version count
- primary key compaction
- update compaction
- delete version
- starrocks_fe_max_tablet_compaction_score
- starrocks_be_tablet_base_max_compaction_score
- starrocks_be_tablet_cumulative_max_compaction_score
- starrocks_be_tablet_update_max_compaction_score
tools:
- top
- curl
- wc
- grep
- tail
related_cases:
- case-014-scan-skew
- case-003-fe-deadlock
---

# 12 - Compaction Troubleshooting (Shared-Nothing)

This skill applies to **shared-nothing** deployments only.
For shared-data, compaction is managed by the storage layer and diagnosed differently.

Three root causes account for the vast majority of cases:

- **Cause A** — import rate exceeds cumulative compaction throughput (rowset pile-up)
- **Cause B** — batch DELETE statements causing version explosion
- **Cause C** — large tablet where base compaction is not self-triggering

---

## Metric Taxonomy — Read This First

Before using any metrics, understand the two-layer structure:

### BE-level metrics (type-specific, per-node)

| Metric name | Meaning |
|---|---|
| `starrocks_be_tablet_cumulative_max_compaction_score` | Highest cumulative compaction score among all tablets on this BE, updated each scheduler round |
| `starrocks_be_tablet_base_max_compaction_score` | Highest base compaction score on this BE, updated each scheduler round |
| `starrocks_be_tablet_update_max_compaction_score` | Highest PK (update) compaction score on this BE. **Not forwarded to FE.** |

### FE-level metric (aggregated, cross-node)

| Metric name | Meaning |
|---|---|
| `starrocks_fe_max_tablet_compaction_score` | Max of `max(cumulative_score, base_score)` across all BEs. ⚠️ **Does NOT include update (PK) compaction score** — update scores are never forwarded from BE to FE. |

### How to retrieve metrics

**Option 1 — curl the BE HTTP port (immediate, no Prometheus needed)**

```bash
# Pull all compaction-related metrics from one BE
curl -s "http://<be_ip>:<be_http_port>/metrics" | grep -E "compaction_score|compaction_num"

# Targeted: cumulative, base, update scores on one BE
curl -s "http://<be_ip>:<be_http_port>/metrics" \
  | grep -E "tablet_cumulative_max_compaction_score|tablet_base_max_compaction_score|tablet_update_max_compaction_score"

# Loop across all BEs (replace IPs and port as needed)
for be in 10.0.0.1 10.0.0.2 10.0.0.3; do
  echo "=== $be ==="
  curl -s "http://$be:8040/metrics" \
    | grep -E "tablet_(cumulative|base|update)_max_compaction_score"
done
```

**Option 2 — FE HTTP port (aggregated cross-node value)**

```bash
# FE metric endpoint (default port 8030)
curl -s "http://<fe_ip>:8030/metrics" | grep "fe_max_tablet_compaction_score"
```

**Option 3 — Prometheus + Grafana (for trend analysis)**

```promql
-- FE-level: cluster-wide worst case (non-PK only)
starrocks_fe_max_tablet_compaction_score

-- Per-BE breakdown: which node is the hotspot
starrocks_fe_tablet_max_compaction_score

-- BE-level: cumulative score per node (Cause A signal)
starrocks_be_tablet_cumulative_max_compaction_score

-- BE-level: base score per node (Cause B / C signal)
starrocks_be_tablet_base_max_compaction_score

-- BE-level: PK update score — NOT in FE metric, must query BE directly
starrocks_be_tablet_update_max_compaction_score
```

**Option 4 — SQL (when metrics endpoint is unavailable)**

```sql
-- There is no SHOW PROC path for compaction scores in shared-nothing mode.
-- (SHOW PROC '/compactions' exists but is shared-data only — it reads lake CompactionMgr history.)

-- Infer per-BE pressure from be_tablets instead:
SELECT be_id,
       COUNT(*) AS tablet_count,
       MAX(num_rowset) AS max_rowset,
       SUM(CASE WHEN num_rowset > 100 THEN 1 ELSE 0 END) AS tablets_over_100_rowsets,
       SUM(CASE WHEN num_rowset > 500 THEN 1 ELSE 0 END) AS tablets_over_500_rowsets
FROM information_schema.be_tablets
GROUP BY be_id
ORDER BY max_rowset DESC;
```

**Default HTTP ports**: BE = 8040, FE = 8030. Verify with `SHOW BACKENDS` or `be.conf` / `fe.conf`.

---

**Key rules for interpretation**:

1. `starrocks_fe_max_tablet_compaction_score` is the cluster-wide worst case for non-PK tables. It hides *which BE* and *which type* is the problem — always drill into BE-level metrics next.
2. **PK table blind spot**: If the cluster is primarily PK tables, `starrocks_fe_max_tablet_compaction_score` can show normal while `starrocks_be_tablet_update_max_compaction_score` is critically high. Always check BE-level update score separately for PK tables.
3. BE metrics are refreshed each time the compaction scheduler runs a round. If the scheduler is stuck, BE metrics go stale.
4. Update (PK) compaction scores use a completely different formula and can legitimately be in the thousands — compare version count, not raw score, to judge severity.
5. In **shared-data** mode the FE metric reflects cloud-native compaction state, unrelated to BE-level rowset scoring.

---

## Score Value Reference

**Action threshold: investigate when score stays above 100 continuously.** A momentary spike is normal; only treat it as a problem if it remains above 100 for 5+ minutes of sustained observation.

### Cumulative / Base score

| Score | Status | Action |
|---|---|---|
| < 100 | Healthy, compaction keeping up | None |
| Sustained > 100 | Compaction starting to fall behind | Follow Phase 1, proceed to Phase 3 Cause A |
| Sustained > 500 | Severe backlog, writes at risk of blocking | Trigger manual compaction immediately; expand threads |
| Base score elevated; num_rowset high but data_size_mb tiny per rowset | DELETE version explosion | Phase 3 Cause B |
| Score suddenly doubles | rowset_count > 90% of `tablet_max_versions` — urgency mode | Act immediately to avoid write block |

### Update (PK) score

| Score | Status | Action |
|---|---|---|
| < 100 | Healthy | None |
| Sustained > 100 | Delete accumulation or multi-segment rowsets building up | Check version count and delete ratio |
| 1000+ | Normal range for heavily written PK tables | **Do not judge severity by score** — use VersionCount (active rowsets from SHOW TABLET) < 1000 as the health bar instead |
| -1 | Tablet skipped (compaction in progress, error state, or interval too short) | Check whether tablet is in error state |

---

## Phase 1 — Confirm Compaction Is Behind

### Step 1.1 — Read metrics and identify type

Check in order:

```
1. starrocks_fe_max_tablet_compaction_score  > 100  → drill into BE metrics
2. starrocks_be_tablet_cumulative_max_compaction_score  → rising?  → Cause A signal
3. starrocks_be_tablet_base_max_compaction_score        → high?    → Cause B or C signal
4. starrocks_be_tablet_update_max_compaction_score      → check version count, not raw score
```

**Score pattern → root cause signal**:

| Pattern | Points toward |
|---|---|
| FE metric high; only cumulative BE metric rising | **Cause A** — imports outpacing cumulative compaction |
| FE metric high; cumulative and base both elevated; import rate is moderate | **Cause B** — DELETE-driven version explosion |
| FE metric high; base score high on one BE; cumulative normal | **Cause C** — large tablet not self-triggering base compaction |
| FE metric oscillating; cumulative spikes then drops | Normal: compaction is keeping up but bursty; verify scan MERGE time |
| FE metric low or normal; PK table import/query degrading | ⚠️ PK blind spot: update score not in FE metric — check `starrocks_be_tablet_update_max_compaction_score` directly at each BE |

**Two FE-side metrics — they are different**:
- `starrocks_fe_max_tablet_compaction_score` — single aggregated value, the `max()` across all BEs. Use for alerting.
- `starrocks_fe_tablet_max_compaction_score{backend="<host>:<port>"}` — per-BE breakdown gauge, one time series per backend. Use in Grafana to see which BE is the hotspot.

If metrics are unavailable → proceed to Step 1.2.

---

### Step 1.2 — Scan problem tablets via `information_schema.be_tablets`

Use `num_rowset` from `be_tablets` to find candidate tablets. To distinguish Pattern B (DELETE explosion) from A/C, check `fe.audit.log` for DELETE bursts on the suspect table — that is more direct than any derived metric.

```sql
-- Step 1: find tablets with high rowset count
SELECT
    bt.be_id,
    bt.tablet_id,
    bt.num_rowset,
    bt.num_segment,
    ROUND(bt.data_size / 1024 / 1024, 1) AS data_size_mb,
    tc.table_name,
    tc.table_schema
FROM information_schema.be_tablets bt
LEFT JOIN information_schema.tables_config tc ON bt.table_id = tc.table_id
ORDER BY bt.num_rowset DESC
LIMIT 20;

-- Step 2: for each suspicious tablet, get VersionCount
SHOW TABLET <tablet_id>;
-- Key field: VersionCount — the actual version count tracked by the system
-- Run the detailcmd from the output to see VersionCount across all replicas
```

Match to one of these patterns:

**Pattern A — Cumulative compaction lagging (→ Cause A)**:
```
num_rowset > 100
VersionCount (SHOW TABLET) ≈ num_rowset
data_size_mb proportional to row count
```
Each import creates one rowset; cumulative compaction merges batches. Import rate > compaction throughput = rowsets pile up.

**Pattern B — DELETE version explosion (→ Cause B)**:
```
num_rowset HIGH (e.g., 8500) — each DELETE creates one delete-predicate rowset
data_size_mb TINY relative to num_rowset (e.g., 2 MB total for 8500 rowsets ≈ 0.00024 MB each)
```
Each `DELETE` on a non-PK table creates a delete-predicate rowset — a real entry in `_rs_metas`, so `num_rowset` rises with every DELETE. `VersionCount` (from `SHOW TABLET`) equals `num_rowset` for non-PK tablets (both come from `_rs_metas.size()`). Only base compaction can merge delete-predicate rowsets. Check the data-per-rowset ratio: < 0.01 MB/rowset strongly indicates DELETE explosion rather than import pile-up.

**Pattern C — Large tablet, base not triggering (→ Cause C)**:
```
num_rowset elevated, data_size_mb in GB range
data_size_mb / num_rowset is large (normal data rowsets, not delete-predicate rowsets)
```
Base compaction ratio trigger not met; only runs on 24h periodic timer.

**Pattern: healthy**:
```
num_rowset < 20 and VersionCount < 50 for all tablets
```

---

### Step 1.3 — Fallback: Read `be.INFO` log

```bash
# What is the scheduler selecting right now?
grep "Found the best tablet to compact" be.INFO | tail -50
# Log format:
#   Found the best tablet to compact. compaction_type=cumulative tablet_id=132561 highest_score=59
#   Found the best tablet to compact. compaction_type=base     tablet_id=238743 highest_score=34
#   Found the best tablet to compact. compaction_type=update   tablet_id=864228 highest_score=19
#
# compaction_type=cumulative repeatedly → Cause A
# compaction_type=base on same tablet repeatedly → Cause B or C
# compaction_type=update with score > 1000 → check version count, not score

# Rank tablets by score
grep "Found the best tablet to compact" be.INFO \
  | grep -oE "tablet_id=[0-9]+ highest_score=[0-9]+" \
  | sort -t= -k4 -rn \
  | head -20

# Find tablets already hitting the version error
grep "too many versions" be.INFO \
  | grep -oE "tablet_id: [0-9]+" \
  | sort | uniq -c | sort -rn | head -10

# Find tablets with score > 200 (significant backlog)
grep -E "highest_score=[2-9][0-9]{2,}" be.INFO | tail -30

# Size-tiered policy: find urgency-mode tablets (rowset_count > 90% of tablet_max_versions)
grep "reached_max_versions=true" be.INFO | tail -20

# Find tablets skipped repeatedly (compaction pool under pressure)
grep "skip tablet" be.INFO | grep -oE "tablet:[0-9]+" | sort | uniq -c | sort -rn | head -10

# Find compaction tasks being submitted (VLOG level 2 required)
grep "submit task to compaction pool" be.INFO | tail -20
# Format: task_id:<id>, tablet_id:<id>, compaction_type:<type>, compaction_score:<score>
```

---

## Phase 2 — Locate and Characterize the Problem Tablet

### Step 2.1 — Get table and partition context

```sql
-- Run on Leader FE
SHOW TABLET <tablet_id>;
-- Key fields: DbName, TableName, PartitionName, State, VersionCount, detailcmd

-- Run the detailcmd shown in above output, e.g.:
SHOW PROC '/dbs/10089/10092/10094';
-- Check: VersionCount across all 3 replicas (should be similar)
-- Note the CompactionStatus URL and open in browser:
--   last_base_compaction_time   (Cause C signal if > 24h)
--   last_cumulative_compaction_time
--   cumulative_layer_point      (boundary between base and cumulative)

-- Determine table type (critical for Cause B vs PK compaction path)
SELECT table_name, table_type, engine
FROM information_schema.tables
WHERE table_schema = '<db_name>' AND table_name = '<table_name>';
-- table_type = 'PRIMARY KEY' → PK path (update compaction, no delete-predicate versions)
-- Otherwise → non-PK path (cumulative + base compaction)
```

### Step 2.2 — Confirm root cause

```sql
-- For non-PK tables: num_rowset == num_version == VersionCount (all from _rs_metas.size())
-- For PK tables: num_version = all edit versions ever; num_rowset = active rowsets in latest version
SELECT tablet_id, num_rowset, num_version, num_segment,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb
FROM information_schema.be_tablets
WHERE tablet_id = <tablet_id>;
```

#### Cause A — Import rate exceeds cumulative compaction throughput

**Confirm all match**:
- `num_rowset > 50`, `data_size_mb` proportional to expected row count
- `starrocks_be_tablet_cumulative_max_compaction_score` is the dominant rising metric
- Log shows `compaction_type=cumulative` for this tablet

**Mechanism**: Each import creates one rowset. Cumulative compaction merges batches of
`min_cumulative_compaction_num_singleton_deltas` to `max_cumulative_compaction_num_singleton_deltas` rowsets.
When import throughput > compaction throughput, `num_rowset` rises continuously.
At query time, MERGE over all rowsets is in-memory — MERGE time rises linearly with `num_rowset`.
Queries slow 5–20× before any error occurs.

Sub-pattern A1 (import-dominant): disk IO < 70% → add compaction threads or batch imports larger.
Sub-pattern A2 (pool-saturated): disk IO > 90%, threads running but `num_rowset` still climbing
→ reduce `max_compaction_concurrency` to improve per-task IO throughput, then throttle imports.

→ **Go to Phase 3, Cause A**

#### Cause B — Batch DELETE statements creating version explosion

**Confirm all match**:
- `num_rowset > 100`, `fe.audit.log` shows DELETE burst (delete-predicate rowsets have near-zero data)
- Table type is **non-PK** (for non-PK: `num_rowset == num_version == VersionCount`; PK uses delete vectors)
- `fe.audit.log` shows DELETE burst on this table

```bash
# Count DELETE statements on this table (last 24h)
grep -i "delete from <table_name>" fe.audit.log | grep "$(date +%Y-%m-%d)" | wc -l
# If count > 500 in a short window → Cause B confirmed

# Find the time window of the burst
grep -i "delete from <table_name>" fe.audit.log \
  | awk '{print $1, $2}' | cut -c1-16 \
  | sort | uniq -c | sort -rn | head -20
# Example: 850 2025-05-25 02: → 850 DELETEs in one minute at 2AM
```

**Mechanism**: For non-PK tables, each `DELETE WHERE id = 1` — even single-row — creates a
delete-predicate rowset stored in `_rs_metas`. This increments both `num_rowset` and `num_version`
equally (they always equal `_rs_metas.size()` for non-PK tablets). Cumulative compaction ignores
delete-predicate rowsets entirely; only base compaction can apply and merge them. Base compaction
is 5–10× more IO-intensive than cumulative (full segment rewrite). With 8000+ delete rowsets, base
compaction runs continuously and saturates disk IO. Once `num_rowset` > `tablet_max_versions`
(default 1000), new imports block with "too many versions".

→ **Go to Phase 3, Cause B**

#### Cause C — Large tablet, base compaction not self-triggering

**Confirm all match**:
- `num_rowset` elevated, `data_size_mb` in GB range
- `data_size_mb` in GB range (normal data rowsets, not delete-predicate rowsets)
- CompactionStatus URL shows `last_base_compaction_time` > 24h ago

**Mechanism**: Base compaction triggers when cumulative layer size > `base_cumulative_delta_ratio`
(default 0.3) × base layer size. For large, slowly-written tablets the cumulative layer stays
below 30% of the multi-GB base for a long time. Base only runs on the 24h periodic timer.
The next time it fires, it processes a very large segment, causing a sudden IO spike.

→ **Go to Phase 3, Cause C**

---

## Phase 3 — Take Action by Cause

### Cause A — Import too frequent / Compaction threads insufficient

**Immediate: Manual compaction**

```bash
# Non-PK table: cumulative compaction on the problem tablet
curl -XPOST "http://<be_ip>:<be_http_port>/api/compact?compaction_type=cumulative&tablet_id=<tablet_id>"

# Non-PK table: base compaction
curl -XPOST "http://<be_ip>:<be_http_port>/api/compact?compaction_type=base&tablet_id=<tablet_id>"

# PK table: update compaction
curl -XPOST "http://<be_ip>:<be_http_port>/api/compact?compaction_type=update&tablet_id=<tablet_id>"
```

**Or ADMIN EXECUTE for coarser granularity (v2.5.6+)**:

```sql
-- Step 1: Get backend_id
SHOW BACKENDS;

-- By partition (most common)
ADMIN EXECUTE ON <backend_id> '
  StorageEngine.submit_manual_compaction_task_for_partition(<partition_id>, 0)
';

-- By table
ADMIN EXECUTE ON <backend_id> '
  StorageEngine.submit_manual_compaction_task_for_table(<table_id>, 0)
';

-- By tablet
ADMIN EXECUTE ON <backend_id> '
  StorageEngine.submit_manual_compaction_task_for_tablet(<tablet_id>, 0)
';

-- Check status
ADMIN EXECUTE ON <backend_id> '
  System.print(StorageEngine.get_manual_compaction_status())
';
```

**Increase compaction throughput dynamically (no restart)**:

```sql
-- v3.2+: apply to all BEs at once via SQL

-- Sub-pattern A1 (disk IO < 70%): raise concurrency
UPDATE information_schema.be_configs SET value = 8
WHERE name = 'max_compaction_concurrency';

UPDATE information_schema.be_configs SET value = 2
WHERE name = 'cumulative_compaction_check_interval_seconds';

-- PK table: increase update compaction threads (can only increase, not decrease at runtime)
UPDATE information_schema.be_configs SET value = 2
WHERE name = 'update_compaction_num_threads_per_disk';

-- Sub-pattern A2 (disk IO > 90%): REDUCE concurrency to improve per-task throughput
UPDATE information_schema.be_configs SET value = 4
WHERE name = 'max_compaction_concurrency';
```

```bash
# v2.5+ per-BE equivalent
curl -XPOST "http://<be_ip>:<be_http_port>/api/update_config?max_compaction_concurrency=8"
curl -XPOST "http://<be_ip>:<be_http_port>/api/update_config?cumulative_compaction_check_interval_seconds=2"
```

**Reduce import frequency**:

```sql
-- Routine Load: increase batch interval (fewer rowsets per minute)
ALTER ROUTINE LOAD FOR <job_name>
PROPERTIES (
  "max_batch_interval" = "60",        -- default 10s → 60s
  "desired_concurrent_number" = "3"   -- reduce parallelism
);
```

For Flink: increase `sink.buffer-flush.interval-ms` (default 300000 ms) and
`sink.buffer-flush.maxrows` (default 500000) to batch more rows per flush.

---

### Cause B — Batch DELETE statements (version explosion)

**Step B-1: Stop the DELETE workload**

```bash
# Identify source of DELETE statements
grep -i "delete from <table_name>" fe.audit.log \
  | awk -F'|' '{print $4}' | sort | uniq -c | sort -rn
# Output: user / IP issuing the DELETEs → kill at application level

# Confirm DELETE traffic has stopped
grep -i "delete from <table_name>" fe.audit.log | tail -5
```

**Step B-2: Assess recovery path**

```sql
-- For non-PK: num_rowset == VersionCount (both from _rs_metas.size()); use num_rowset as the recovery gauge
SELECT tablet_id, num_rowset,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb,

FROM information_schema.be_tablets
WHERE tablet_id = <tablet_id>;

-- num_rowset < 5000  → base compaction can recover in hours → Option B-2b
-- num_rowset > 10000 → DROP TABLE is faster → Option B-2a
```

**Option B-2a — Fast recovery: DROP TABLE FORCE**

```sql
-- 1. Back up if needed
INSERT INTO <backup_table> SELECT * FROM <table_name>;

-- 2. Force drop — bypasses recycle bin, triggers immediate metadata cleanup
DROP TABLE <table_name> FORCE;

-- 3. Recreate and re-import
```

Score drops immediately; cluster recovers within minutes.

**Option B-2b — Wait with tuned compaction**

```sql
-- Shorten base compaction check interval
UPDATE information_schema.be_configs SET value = 10
WHERE name = 'base_compaction_check_interval_seconds';   -- default 60

-- Increase base compaction threads
UPDATE information_schema.be_configs SET value = 3
WHERE name = 'base_compaction_num_threads_per_disk';     -- default 1, static but can be set dynamically on v3.2+

-- Monitor: num_rowset should decrease by hundreds per interval as base compaction merges delete rowsets
SELECT tablet_id, num_rowset,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb,

FROM information_schema.be_tablets
WHERE tablet_id = <tablet_id>;
-- Run every 5 minutes; if num_rowset not decreasing after 30min → check for base compaction errors
```

```bash
# Watch base compaction progress in log
grep "base.*tablet_id=<tablet_id>\|compact done.*<tablet_id>" be.INFO | tail -20

# If not progressing, check for errors
grep "base compaction.*error\|base compaction.*fail" be.INFO | tail -20
```

**Prevent future recurrence**: Replace per-row DELETE loops:

```sql
-- Instead of: DELETE FROM t WHERE id = 1; (repeated 10000x)
-- Use:  DELETE FROM t WHERE id IN (1, 2, 3, ...) LIMIT 10000;
-- Or:   ALTER TABLE t DROP PARTITION <old_partition>;  -- zero-copy, no compaction
-- Or:   Migrate to Primary Key model (DELETEs use delete vectors, not predicate versions)
```

---

### Cause C — Large tablet, base compaction not triggering

```bash
# Force base compaction on the tablet immediately
curl -XPOST "http://<be_ip>:<be_http_port>/api/compact?compaction_type=base&tablet_id=<tablet_id>"
```

```sql
-- Lower the size-ratio trigger so base compaction fires sooner
UPDATE information_schema.be_configs SET value = 0.1
WHERE name = 'base_cumulative_delta_ratio';  -- default 0.3; lower = trigger sooner

-- Shorten the periodic base compaction interval
UPDATE information_schema.be_configs SET value = 43200   -- 12h; default 86400 (24h)
WHERE name = 'base_compaction_interval_seconds_since_last_operation';
```

---

## Phase 4 — Verify Recovery

```sql
-- num_rowset should be declining; for non-PK tablets num_rowset == VersionCount
SELECT tablet_id, num_rowset, num_segment,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb
FROM information_schema.be_tablets
WHERE tablet_id = <tablet_id>;

-- Confirm no new import failures from "too many versions"
SELECT LABEL, STATE, ERROR_MSG
FROM information_schema.loads
WHERE STATE = 'FAILED'
ORDER BY LOAD_FINISH_TIME DESC
LIMIT 10;
```

```bash
# Confirm compaction tasks are completing
grep "compact done\|compaction finished" be.INFO | tail -20

# Verify score is dropping (if VLOG=2 enabled)
grep "submit task to compaction pool.*compaction_score" be.INFO | tail -20
```

Grafana: `starrocks_fe_max_tablet_compaction_score` should trend downward.
Target: `starrocks_be_tablet_cumulative_max_compaction_score` sustained below 100.

---

## Key Configuration Parameters

### Non-Primary Key Tables

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `cumulative_compaction_num_threads_per_disk` | 1 | Cumulative threads per disk | No (restart) |
| `base_compaction_num_threads_per_disk` | 1 | Base threads per disk | No (restart) |
| `max_compaction_concurrency` | -1 (unlimited) | Global compaction thread cap | Yes (v2.5+) |
| `cumulative_compaction_check_interval_seconds` | 1 | Cumulative poll interval | Yes (v2.5+) |
| `base_compaction_check_interval_seconds` | 60 | Base poll interval | Yes (v2.5+) |
| `min_cumulative_compaction_num_singleton_deltas` | 5 | Min rowsets to trigger cumulative | Yes (v2.5+) |
| `max_cumulative_compaction_num_singleton_deltas` | 1000 | Max rowsets per cumulative task | Yes (v2.5+) |
| `base_cumulative_delta_ratio` | 0.3 | Cumulative/Base size ratio trigger | Yes (v2.5+) |
| `base_compaction_interval_seconds_since_last_operation` | 86400 | Periodic base compaction timer | Yes (v2.5+) |
| `tablet_max_versions` | 1000 | Max versions before write block | Yes (v2.3+) |
| `max_compaction_candidate_num` | 40960 | Max tablets in candidate pool | Yes (v2.5+) |
| `enable_size_tiered_compaction_strategy` | true | Use size-tiered policy (recommended) | No (restart) |
| `size_tiered_min_level_size` | 131072 (128KB) | Smallest level size | No (restart) |
| `size_tiered_level_multiple` | 5 | Size ratio between consecutive levels | No (restart) |
| `size_tiered_level_num` | 7 | Number of levels (max ≈ 128KB × 5^7 ≈ 20GB) | No (restart) |

### Primary Key Tables

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `update_compaction_num_threads_per_disk` | 1 | Update compaction threads per disk | Yes (increase only) |
| `update_compaction_check_interval_seconds` | 10 | Update compaction poll interval | Yes (v2.5+) |
| `update_compaction_per_tablet_min_interval_seconds` | 120 | Min interval between tasks per tablet | Yes (v2.5+) |
| `max_update_compaction_num_singleton_deltas` | 500 | Max rowsets per update task | Yes (v2.5+) |
| `update_compaction_size_threshold` | 268435456 (256MB) | Score normalization factor | Yes (v2.5+) |
| `update_compaction_result_bytes` | 1073741824 (1GB) | Max result size per update task | Yes (v2.5+) |
| `enable_pk_size_tiered_compaction_strategy` | false | Size-tiered policy for PK tables | No (restart) |

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

Static parameters (`*_num_threads_per_disk` for non-PK) must be in `be.conf` and require restart.

---

## Common Issues Quick Reference

| Symptom | Most Likely Cause | First Check |
|---|---|---|
| `too many versions` error on import | Any compaction lag | `be_tablets.num_rowset` vs `tablet_max_versions` (1000) |
| Worsens after 2AM batch job | Cause B — batch DELETE | `fe.audit.log` DELETE count by minute |
| Query MERGE time > 50% of scan time | Cause A — rowset pile-up | `be_tablets.num_rowset` on scanned tables |
| `starrocks_be_tablet_cumulative_max_compaction_score` rising alone | Cause A | Add cumulative threads; batch imports |
| `starrocks_be_tablet_base_max_compaction_score` rising; import rate low | Cause B or C | Check `fe.audit.log` for DELETE burst → Cause B; check `data_size_mb` in GB range → Cause C |
| Score stuck > 200 despite threads running | Cause A-A2 — IO saturated | `iostat` — if IO > 90%, reduce `max_compaction_concurrency` |
| Sudden IO spike on one BE, cluster degrades | Cause C — large tablet base firing | CompactionStatus URL, `last_base_compaction_time` |
| `starrocks_be_tablet_update_max_compaction_score` > 1000 | PK table, normal scoring | Check version count; reduce delete ratio |
| Score > 10000 on PK table | Score formula amplifies delete bytes | Use VersionCount (active rowsets, from SHOW TABLET) < 1000 as health bar |
| CPU saturated by compact_pool threads | `max_compaction_concurrency` too high | Reduce to 4–8 |
| Compaction candidate warning in log | `max_compaction_candidate_num` exceeded | Increase `max_compaction_candidate_num` or reduce tablet count |
| `skip tablet` in log for same tablet repeatedly | Tablet locked or too-soon since last run | Check `_error` state; check `update_compaction_per_tablet_min_interval_seconds` |

---

## Related Cases

- `case-014-scan-skew` — MERGE phase bottleneck from rowset accumulation (Cause A pattern)

## Cross-Skill Guides

- [`guides/cascade-cluster-degradation.md`](../guides/cascade-cluster-degradation.md) — Full cascade: compaction backlog → import slow → BE OOM → FE deadlock. Read this when multiple symptoms appear simultaneously across skills.

---

## Resources

- [Compaction mechanism documentation](https://docs.starrocks.io/docs/administration/management/compaction/)