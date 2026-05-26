---
name: 06-shared-data
description: "Use for shared-data (lake-mode / cloud-native) deployments only — not for shared-nothing. Covers DataCache corruption and autoscaling-induced compaction regressions, S3 multipart upload failures and 503 rate limiting, FE leader switch causing in-flight transaction loss, compaction score management via be_cloud_native_compactions, and cached tablet metadata inconsistencies causing publish failures."
version: 2.0.0
category: shared-data
keywords:
- DataCache
- S3
- FE leader switch
- shared-data
- StarCache
- multipart upload
- vacuum
- compaction score
tools: []
related_cases:
- case-009-stream-load-stuck
- case-010-datacache-corruption
- case-011-multi-root-cause
- case-013-datacache-autoscaling
---

# 06 - Shared-Data Troubleshooting

Investigation guide for shared-data architecture issues: DataCache (StarCache) corruption
and autoscaling regressions, S3 rate limiting and multipart upload failures, FE leader
switch bugs, and compaction score management.

Five root causes account for the vast majority of cases:

- **Cause A** — DataCache autoscaling regression (eviction triggers S3 reads, compaction slows)
- **Cause B** — DataCache corruption (checksum mismatch causing publish or query failures)
- **Cause C** — S3 rate limiting (503 errors or multipart upload failures)
- **Cause D** — FE leader switch issues (EditLog gap causing transaction replay failure)
- **Cause E** — Cached tablet metadata inconsistency (stale cache version mismatch)

---

## Metric Taxonomy — Read This First

Before using any metrics, understand the key observability sources:

### DataCache Metrics

| Metric / Source | Meaning |
|---|---|
| `starrocks_be_diskcache_disk_avail_bytes` | Remaining free bytes on the DataCache disk volume — when near zero, autoscaler evicts data |
| DataCache hit rate | Ratio of cache hits to total accesses — a drop indicates eviction or corruption; visible in Grafana |
| `be.WARNING` — star cache autoscaling | Log messages when DataCache triggers autoscaling eviction |
| `be.WARNING` — `checksum mismatch` / `corrupted compressed block` | Cache block integrity failure — data on disk does not match expected checksum |

### Compaction Metrics (Shared-Data)

| Metric / Source | Meaning |
|---|---|
| `information_schema.be_cloud_native_compactions` | Cloud-native compaction history: tablet, timing, size, status — primary compaction audit table for shared-data |
| `lake_publish_tablet_version_queuing_count` | Number of tablet publish tasks queued — high value means publish is falling behind |
| BE log — compaction score warnings | Score thresholds exceeded; indicates compaction cannot keep up with ingestion |

### S3 / Object Storage Metrics

| Metric / Source | Meaning |
|---|---|
| fslib write IO metrics — p99 latency | S3 PUT/GET tail latency — spikes indicate throttling or network degradation |
| `503 rate limit` errors | S3 request rate exceeded — visible in BE log |
| S3 multipart upload failure log | Partial uploads that leave `.dat` / `.sst` orphan fragments |
| `lake_flush_thread_num_per_store` pool depth | Flush thread pool queue depth — saturation means S3 writes are blocking flush |

### FE Leader Metrics

| Metric / Source | Meaning |
|---|---|
| `fe.log` — leader election messages | FE BDBJE leader transition events |
| `fe.log` — `txn_version` / GTID validation failure | `meta file not found` after leader switch — indicates EditLog gap |
| `fe.log` — `publish version timeout` | Publish tasks not completing after leader switch |

### How to Retrieve Metrics

**DataCache disk and hit rate**

```bash
# DataCache disk available bytes (all BEs)
for be in <be1> <be2> <be3>; do
  echo "=== $be ==="
  curl -s "http://$be:8040/metrics" | grep "diskcache_disk_avail_bytes"
done

# DataCache hit rate (Grafana PromQL)
# starrocks_be_diskcache_hit_count / (starrocks_be_diskcache_hit_count + starrocks_be_diskcache_miss_count)

# DataCache autoscaling log signals
grep "star cache\|StarCache\|autoscal" be.WARNING | tail -30
```

**Compaction status (shared-data)**

```sql
-- Cloud-native compaction history
SELECT * FROM information_schema.be_cloud_native_compactions
ORDER BY start_time DESC
LIMIT 20;

-- Tablet version count — high num_rowset indicates compaction lag
SELECT be_id, tablet_id, num_rowset,
       ROUND(data_size / 1024 / 1024, 1) AS data_size_mb
FROM information_schema.be_tablets
ORDER BY num_rowset DESC
LIMIT 20;
```

```bash
# Publish queue depth
for be in <be1> <be2>; do
  echo "=== $be ==="
  curl -s "http://$be:8040/metrics" | grep "lake_publish_tablet_version_queuing"
done
```

**S3 latency and errors**

```bash
# S3 rate limit errors
grep "503\|Please reduce your request rate\|rate limit" be.WARNING be.log | tail -30

# S3 write latency signals
grep "fslib\|write_latency\|S3.*timeout\|upload.*fail" be.WARNING | tail -30

# Flush backlog
grep "flush.*timeout\|flush.*queue\|lake_flush" be.WARNING | tail -20
```

**FE leader switch signals**

```bash
# Leader election events
grep "leader\|election\|become\|stepdown" fe.log | tail -30

# Post-switch transaction failures
grep "meta file not found\|txn_version\|GTID" fe.log | tail -20

# Publish timeout after switch
grep "publish version.*timeout\|publish.*fail" fe.log | tail -20
```

**Key rules for interpretation**:

1. `starrocks_be_diskcache_disk_avail_bytes` approaching zero is the leading indicator for Cause A — autoscaling eviction follows disk fullness, not cache miss. Monitor this metric proactively.
2. A DataCache hit rate drop from > 90% to < 50% within a short window almost always correlates with either autoscaling eviction (Cause A) or corruption (Cause B) — distinguish by checking `be.WARNING` for autoscaling logs vs checksum errors.
3. S3 503 errors appear in BE logs, not FE logs. If queries slow but FE looks healthy, check BE logs for rate limit messages before escalating.
4. `meta file not found` errors that start within 10 seconds of a leader election event are almost always Cause D (EditLog gap). Check the timing correlation before investigating other causes.

---

## Shared-Data Issue Signal Reference

| Error / Symptom | Root Cause | Phase |
|---|---|---|
| Compaction score rising; S3 GET IOPS spike; query latency increases | Cause A: DataCache autoscaling eviction | Phase 2, Step 2.1 |
| `corrupted compressed block contents` on publish | Cause B: DataCache block corruption | Phase 2, Step 2.2 |
| `checksum mismatch` in BE log; query returns wrong data or BE crashes | Cause B: DataCache corruption | Phase 2, Step 2.2 |
| `503: Please reduce your request rate` in BE log | Cause C: S3 rate limiting | Phase 2, Step 2.3 |
| `.dat` / `.sst` files missing; load job cancelled silently | Cause C: S3 multipart upload failure | Phase 2, Step 2.3 |
| `meta file not found` immediately after FE leader switch | Cause D: EditLog persistence gap | Phase 2, Step 2.4 |
| `txn_version` collision; GTID validation failure | Cause D: FE leader switch race | Phase 2, Step 2.4 |
| `publish version fail because tablet meta file is missing` | Cause E: Cached tablet metadata stale | Phase 2, Step 2.5 |
| BE log: `base version adjusted from N to M` | Cause E: Latest tablet meta cache mismatch | Phase 2, Step 2.5 |
| `lake_publish_tablet_version_queuing_count` high | Publish backlog — can be Cause A, C, or E | Phase 1 |

---

## Phase 1 — Identify Issue Type

**Goal**: classify the issue in under 60 seconds.

### Step 1.1 — Characterize the Failure

```bash
# Is there an active S3 error?
grep "503\|rate limit\|multipart\|upload.*fail" be.WARNING | tail -5
# → Yes: likely Cause C

# Is there a recent FE leader switch?
grep "become leader\|election" fe.log | tail -5
# Note the timestamp — compare to when errors started
grep "meta file not found\|txn_version" fe.log | tail -5
# → Leader switch within 10s of errors: Cause D

# Is compaction score rising?
curl -s http://<be>:8040/metrics | grep "compaction_score\|lake_publish"
# Also check:
grep "star cache\|autoscal" be.WARNING | tail -5
# → Autoscaling log + rising score: Cause A

# Is there a checksum or corruption error?
grep "checksum\|corrupted\|corruption" be.WARNING be.log | tail -5
# → Yes: Cause B

# Is there a tablet meta cache mismatch?
grep "base version adjusted\|tablet meta file is missing\|The specified key does not exist" be.WARNING | tail -5
# → Yes: Cause E
```

| Observation | Issue Type |
|---|---|
| `503` or `rate limit` in BE log | Cause C — S3 rate limiting |
| `meta file not found` + recent leader switch | Cause D — FE leader switch |
| Rising compaction score + autoscaling log | Cause A — DataCache autoscaling |
| `corrupted compressed block` or `checksum mismatch` | Cause B — DataCache corruption |
| `tablet meta file is missing` + `base version adjusted` | Cause E — Cached tablet metadata |
| High `lake_publish_tablet_version_queuing_count` | Could be A, C, or E — investigate further |

---

## Phase 2 — Confirm Root Cause

### Step 2.1 — DataCache Autoscaling Regression

```bash
# Check disk utilization on cache volume
df -h <datacache_disk_path>

# Check autoscaling events in log
grep "star cache\|autoscal\|evict" be.WARNING | grep -v "^#" | tail -30

# Check hit rate drop timing
# In Grafana: overlay diskcache_disk_avail_bytes with DataCache hit rate
# Hit rate drop should follow disk fullness event
```

```sql
-- Check compaction score correlation
SELECT * FROM information_schema.be_cloud_native_compactions
WHERE start_time > NOW() - INTERVAL 1 HOUR
ORDER BY start_time DESC;
```

**Confirm all match for Cause A**:
- `starrocks_be_diskcache_disk_avail_bytes` was near zero before the incident
- `be.WARNING` shows autoscaling eviction messages
- DataCache hit rate dropped at the same time compaction score rose
- S3 GET IOPS increased (compaction reading from remote instead of cache)

→ **Go to Phase 3, Cause A**

### Step 2.2 — DataCache Corruption

```bash
# Look for corruption errors
grep "corrupted compressed block\|checksum mismatch\|corruption" be.WARNING be.log | tail -20

# Identify affected tablet / block
grep "corrupted compressed block" be.WARNING | grep -oE "tablet_id=[0-9]+"

# Check if this is hardware-related (multiple disks affected)
grep "checksum mismatch" be.WARNING | grep -oE "block_path=[^ ]+" | sort | uniq
```

**Confirm all match for Cause B**:
- `be.WARNING` contains `corrupted compressed block` or `checksum mismatch`
- Errors are reproducible on the same BE (not transient network issue)
- Publish or query fails specifically when accessing the cached block

→ **Go to Phase 3, Cause B**

### Step 2.3 — S3 Rate Limiting

```bash
# Count 503 errors per minute to confirm rate limiting
grep "503\|Please reduce your request rate" be.WARNING \
  | awk '{print $1, $2}' | cut -c1-16 \
  | sort | uniq -c | sort -rn | head -10

# Check multipart upload failures
grep "multipart\|upload.*fail\|dat.*missing\|sst.*missing" be.WARNING | tail -20

# Check flush backlog
grep "flush.*timeout\|lake_flush" be.WARNING | tail -20
```

**Confirm all match for Cause C (rate limiting)**:
- `be.WARNING` contains `503: Please reduce your request rate`
- Errors correlate with high-throughput ingestion windows
- S3 prefix configuration has no partitioning (`num_partitioned_prefix` not set or = 1)

**Confirm all match for Cause C (multipart upload)**:
- BE log shows multipart upload timeout or silent failure
- `.dat` or `.sst` files are missing after load job claims success
- `lake_metadata_cache_limit` is small (< 2 GB) for cluster size

→ **Go to Phase 3, Cause C**

### Step 2.4 — FE Leader Switch Issues

```bash
# Find leader switch timestamp
grep "become leader\|I am leader\|stepdown\|election" fe.log \
  | tail -10

# Find first error after switch
grep "meta file not found\|txn_version\|GTID\|publish.*fail" fe.log \
  | tail -20

# Confirm timing: error should appear within seconds of leader switch
```

**Confirm all match for Cause D**:
- FE leader switch event visible in `fe.log`
- `meta file not found` or GTID validation failure appears within 10 s of switch
- Root cause is EditLog write failure during graceful exit of previous leader

→ **Go to Phase 3, Cause D**

### Step 2.5 — Cached Tablet Metadata Inconsistency

```bash
# Look for the specific error pattern
grep "tablet meta file is missing\|The specified key does not exist" be.WARNING | tail -20

# Look for cache version adjustment log
grep "base version adjusted" be.WARNING | tail -20
# Format: "base version adjusted from N to M, because the latest tablet meta cache has meta which version is M"

# Identify affected tablets
grep "base version adjusted" be.WARNING | grep -oE "tablet_id=[0-9]+" | sort | uniq
```

**Confirm all match for Cause E**:
- `be.WARNING` shows `base version adjusted from N to M`
- The actual S3 meta file version (N) is lower than what the cache reports (M)
- Issue is specific to 4.0.x versions
- Restarting the BE clears the cache and resolves the mismatch

→ **Go to Phase 3, Cause E**

---

## Phase 3 — Take Action by Cause

### Cause A — DataCache Autoscaling Regression

**Confirm all match**:
- DataCache disk utilization > 90% triggered autoscaling eviction
- Cache hit rate dropped; compaction score rose in correlation
- Compaction reads moved from local cache to S3, reducing throughput

**Mechanism**: When the DataCache disk volume fills beyond the high-water mark (`datacache_disk_high_level`, default 80%), the autoscaler evicts the least-recently-used cache blocks. If hot tablet data is evicted, subsequent compaction tasks cannot read from local cache and must fetch rowsets from S3. S3 reads are 10–100× slower than local NVMe reads, causing compaction throughput to collapse. As compaction falls behind, tablet version count rises, and queries must merge more versions — amplifying query latency.

**Immediate: Disable autoscaling on the affected warehouse**

```sql
-- Disable DataCache autoscaling (requires BE config change)
-- Add to be.conf:
-- datacache_auto_adjust_enable = false

-- Or update dynamically (v3.2+):
UPDATE information_schema.be_configs SET value = 'false'
WHERE name = 'datacache_auto_adjust_enable';
```

**Reduce cache watermark to prevent triggering**

```bash
# Lower the eviction threshold (default 80%)
# Add to be.conf:
datacache_disk_high_level = 70
datacache_disk_low_level = 60

# Or dynamically:
curl -XPOST "http://<be_ip>:8040/api/update_config?datacache_disk_high_level=70"
curl -XPOST "http://<be_ip>:8040/api/update_config?datacache_disk_low_level=60"
```

**Expand cache disk capacity**

```bash
# Add additional cache disk volumes to be.conf:
storage_root_path = /data1/cache;/data2/cache;/data3/cache
# datacache_disk_size applies per volume
```

**Pin hot tablets to cache**

```sql
-- Set cache priority for hot tables (v3.3+)
ALTER TABLE <hot_table> SET ("datacache_priority" = "high");
```

---

### Cause B — DataCache Corruption

**Confirm all match**:
- `be.WARNING` contains `corrupted compressed block contents` or `checksum mismatch`
- Errors are reproducible on the same BE node
- Publish or query fails when accessing the specific cached block

**Mechanism**: A DataCache block on disk does not match its expected checksum. This can result from a disk hardware fault (bad sectors, write error), a kernel page cache corruption (power failure during flush), or an abnormal BE shutdown that left a partially written block. When the BE reads the block and checksum validation fails, it either returns an error (causing publish or query failure) or, if validation is bypassed, returns wrong data silently.

**Immediate: Clear DataCache and restart BE**

```bash
# Step 1: Stop the BE
./bin/stop_be.sh

# Step 2: Remove the corrupt DataCache directory
# Find the cache path from be.conf (storage_root_path or datacache_disk_path)
rm -rf <datacache_disk_path>/*

# Step 3: Restart BE (data will be re-read from S3 on next access)
./bin/start_be.sh

# Step 4: Confirm BE rejoins
SHOW BACKENDS;
```

**Check disk health**

```bash
# Check for disk errors
dmesg | grep -i "error\|fault\|bad sector\|ata\|scsi" | tail -30

# Check SMART data
smartctl -a /dev/<disk_device>

# If disk is faulty — replace disk before re-enabling DataCache
```

**Long-term: ensure checksum validation is enabled**

```bash
# Verify in be.conf (default is enabled):
# datacache_checksum_enable = true
grep "datacache_checksum" be.conf
```

---

### Cause C — S3 Rate Limiting

**Confirm all match**:
- `be.WARNING` contains `503: Please reduce your request rate`
- Errors correlate with high-throughput ingestion periods
- S3 storage volume does not use partitioned prefix

**Mechanism**: AWS S3 (and most S3-compatible object stores) partition namespace by key prefix. When all objects share the same prefix, a single partition handles all requests. S3 limits each prefix partition to approximately 3,500 PUT/COPY/POST/DELETE and 5,500 GET/HEAD requests per second. Heavy ingestion can exceed these limits, causing S3 to return 503 responses. The BE retries, but sustained 503 responses block flush threads and back up the memtable pipeline.

**Fix: Partitioned prefix**

```sql
-- Create a storage volume with partitioned prefix (distributes objects across S3 partitions)
CREATE STORAGE VOLUME sv_partitioned
TYPE = S3
LOCATIONS = ('s3://<bucket>/<path>')
PROPERTIES (
  "aws.s3.region" = "<region>",
  "aws.s3.access_key" = "<key>",
  "aws.s3.secret_key" = "<secret>",
  "num_partitioned_prefix" = "100"  -- 100 prefix shards
);
```

**Reduce per-upload size**

```bash
# Increase mutable_bucket_num to reduce per-upload object size (default 7)
# Add to be.conf:
lake_mutable_bucket_num = 16

# Or dynamically:
curl -XPOST "http://<be_ip>:8040/api/update_config?lake_mutable_bucket_num=16"
```

**Fix: S3 multipart upload failures**

```bash
# Increase metadata cache limit for larger clusters
# Add to be.conf:
lake_metadata_cache_limit = 8589934592  # 8 GB

# Set conservative vacuum to avoid premature cleanup of partial uploads
# In fe.conf or via SQL:
lake_autovacuum_grace_period_minutes = 4320  # 3 days
```

```sql
-- Set vacuum grace period via SQL (v3.2+)
ADMIN SET FRONTEND CONFIG ("lake_autovacuum_grace_period_minutes" = "4320");
```

---

### Cause D — FE Leader Switch Issues

**Confirm all match**:
- FE leader switch visible in `fe.log`
- `meta file not found` or GTID validation failure within seconds of switch
- Root cause: previous FE leader's graceful exit triggered EditLog write, but the write may not have been applied before the new leader started replaying

**Mechanism**: During FE graceful exit, the outgoing leader may flush pending EditLog entries. If a publish task is dispatched after the EditLog write but before the leader finishes exiting, the new leader cannot replay the corresponding transaction (the `txn_version` refers to metadata that the new leader cannot locate). This results in `meta file not found` errors and failed publish tasks for load jobs that were in-flight during the switch.

**Immediate: Disable FE graceful exit**

```bash
# Add to fe.conf on all FE nodes:
graceful_exit_timeout_seconds = 0  # disable graceful exit
# Or:
enable_graceful_exit = false

# Apply on next FE restart
```

**Retry failed load jobs**

```sql
-- Find failed load jobs around the leader switch time
SELECT LABEL, STATE, ERROR_MSG, CREATE_TIME, FINISH_TIME
FROM information_schema.loads
WHERE STATE = 'FAILED'
  AND FINISH_TIME > '<leader_switch_timestamp>'
ORDER BY FINISH_TIME;

-- Retry by re-submitting the load job with the same label
```

**Monitor for recurrence**

```bash
# After fix, verify no new meta file errors
grep "meta file not found\|txn_version" fe.log | tail -5
```

---

### Cause E — Cached Tablet Metadata Inconsistency

**Confirm all match**:
- BE log shows `base version adjusted from N to M`
- `publish version fail because tablet meta file is missing — The specified key does not exist`
- Cluster is running StarRocks 4.0.x
- Issue is persistent until BE restart

**Mechanism**: The "latest tablet meta cache" on the BE holds a pointer to a specific tablet metadata version (M). However, the actual metadata file on S3 only contains up to version N (N < M). This mismatch can occur due to a software bug in 4.0.x where the cache is not properly invalidated after a failed publish. When compaction or publish attempts to read version M from S3, the file does not exist, causing `The specified key does not exist` errors.

**Immediate: Restart the affected BE**

```bash
# Restart BE to reset the in-memory metadata cache
./bin/stop_be.sh && sleep 5 && ./bin/start_be.sh

# Confirm BE rejoins
SHOW BACKENDS;

# Confirm publish errors resolve
grep "tablet meta file is missing" be.WARNING | tail -5
```

**Long-term: Upgrade out of 4.0.x**

```bash
# This is a known bug class in 4.0.x — upgrade to a patched version
# See case-009-stream-load-stuck for full investigation details
```

---

## Phase 4 — Verify Recovery

**DataCache recovery (Cause A / Cause B)**

```bash
# DataCache hit rate should recover to > 80%
# Monitor in Grafana: starrocks_be_diskcache_hit_count / total

# Disk availability should be above low watermark
for be in <be1> <be2>; do
  curl -s "http://$be:8040/metrics" | grep "diskcache_disk_avail_bytes"
done

# Compaction score should be declining
SELECT * FROM information_schema.be_cloud_native_compactions
ORDER BY start_time DESC LIMIT 10;
```

**S3 recovery (Cause C)**

```bash
# No new 503 errors
grep "503\|rate limit" be.WARNING | tail -5

# Flush backlog cleared
for be in <be1> <be2>; do
  curl -s "http://$be:8040/metrics" | grep "lake_publish_tablet_version_queuing"
done
# Should be near zero
```

**Leader switch recovery (Cause D)**

```bash
# No new meta file errors
grep "meta file not found\|txn_version" fe.log | tail -5

# Failed load jobs identified and retried
SELECT STATE, COUNT(*) FROM information_schema.loads
GROUP BY STATE;
```

**Metadata cache recovery (Cause E)**

```bash
# No new "base version adjusted" entries since restart
grep "base version adjusted" be.WARNING | tail -5

# Publish succeeds for new load jobs
SELECT LABEL, STATE FROM information_schema.loads
WHERE STATE IN ('FINISHED', 'FAILED')
ORDER BY FINISH_TIME DESC LIMIT 10;
```

---

## Key Configuration Parameters

### Shared-Data Key Config

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `datacache_auto_adjust_enable` | true | Enable DataCache autoscaling eviction | Yes |
| `datacache_disk_high_level` | 80 | Disk utilization % at which autoscaling starts eviction | Yes |
| `datacache_disk_low_level` | 60 | Target disk utilization % after eviction | Yes |
| `datacache_checksum_enable` | true | Enable DataCache block checksum validation | No (restart) |
| `datacache_disk_size` | -1 (unlimited) | Max bytes per cache disk volume | No (restart) |
| `lake_mutable_bucket_num` | 7 | Number of mutable buckets per tablet (affects per-upload object size) | No (restart) |
| `lake_metadata_cache_limit` | 2 GB | Max bytes of tablet metadata in BE memory cache | No (restart) |
| `lake_flush_thread_num_per_store` | 4 | Flush threads per storage volume | No (restart) |
| `lake_autovacuum_grace_period_minutes` | 1440 | Grace period before vacuuming orphan objects (1 day default) | Yes (FE config) |
| `num_partitioned_prefix` | 0 (no partitioning) | S3 prefix shards to distribute request rate | No (storage volume DDL) |
| `enable_graceful_exit` | true | Whether FE performs graceful shutdown | No (restart) |
| `graceful_exit_timeout_seconds` | 120 | Max seconds for graceful FE shutdown | No (restart) |

### Applying Dynamic Parameters

```sql
-- BE config (v3.2+): apply to all BEs at once
UPDATE information_schema.be_configs SET value = 'false'
WHERE name = 'datacache_auto_adjust_enable';

SELECT * FROM information_schema.be_configs
WHERE name = 'datacache_auto_adjust_enable';
```

```bash
-- BE config (v2.5+): apply per BE
curl -XPOST "http://<be_ip>:8040/api/update_config?datacache_auto_adjust_enable=false"
curl "http://<be_ip>:8040/varz" | grep datacache_auto_adjust  # verify
```

```sql
-- FE config: apply dynamically
ADMIN SET FRONTEND CONFIG ("lake_autovacuum_grace_period_minutes" = "4320");
ADMIN SHOW FRONTEND CONFIG LIKE "lake_autovacuum%";
```

---

## Common Issues Quick Reference

| Issue | Cause | Fix |
|---|---|---|
| Compaction score climbing during steady-state ingestion | DataCache autoscaling evicting hot data | Disable autoscaling on ingestion warehouse |
| `corrupted compressed block` on publish | StarCache corruption | Clear DataCache; restart BE |
| `meta file not found` immediately after leader switch | EditLog persistence bug on graceful exit | Disable FE graceful exit |
| `.dat` / `.sst` file missing | S3 multipart upload failed silently | Conservative vacuum; monitor loss metrics |
| S3 503 on heavy ingest | S3 partition request rate exceeded | `num_partitioned_prefix=100`; reduce per-upload size |
| High `lake_publish_tablet_version_queuing_count` | Publish backlog from S3 slowness or compaction lag | Increase flush threads; check S3 latency |
| `base version adjusted from N to M` | Cached tablet metadata version mismatch (4.0.x) | Restart BE; upgrade to patched version |

---

## Related Cases

- `case-009-stream-load-stuck` — cached tablet meta bug in 4.0.2
- `case-010-datacache-corruption` — silent StarCache corruption causing publish failures
- `case-011-multi-root-cause` — leader-switch GTID collision plus S3 multipart failures
- `case-013-datacache-autoscaling` — autoscaling-induced compaction regression

---

## Causal Chains

### Chain 1: DataCache Autoscaling → Eviction → Compaction Reads S3 → Score Rises → Slow Queries

```
Disk utilization > 90% on cache volume
  ↓ observable: starrocks_be_diskcache_disk_avail_bytes near zero in Grafana
DataCache autoscaler evicts hot tablet data blocks
  ↓ observable: DataCache hit rate drops in Grafana dashboard
Compaction reads evicted rowsets from S3 instead of local cache
  ↓ observable: fslib write IO metrics show elevated S3 read latency; compaction bytes/sec drops
Compaction score rises; tablet version count increases
  ↓ observable: BE log compaction score warnings; SELECT num_rowset FROM information_schema.be_tablets ORDER BY num_rowset DESC shows high counts
Scan node cache miss; every scan re-reads from S3
  ↓ observable: DataCache hit rate remains low; Profile shows high IOTaskWaitTime in OLAP_SCAN_NODE
User sees slow queries with elevated latency
```
**Trigger conditions**: Cache volume disk utilization exceeds 90%; hot data larger than cache capacity.

**Break point**: Expand cache disk capacity; reduce cache watermark via `datacache_disk_high_level`; pin hot tablets to cache with priority policy.

---

### Chain 2: S3 Write Latency Spike → Flush Backlog → Load Timeout

```
S3 backend experiences write latency spike (network jitter or throttling)
  ↓ observable: fslib write IO metrics show p99 S3 PUT latency spike
Memtable flush IO wait increases; flush tasks queue behind slow S3 writes
  ↓ observable: lake_flush_thread_num_per_store pool queue depth growing; BE log shows flush thread pool saturation
Flush backlog grows; new memtable flushes cannot proceed
  ↓ observable: memtable memory usage rising; BE log flush timeout warnings
Stream load / broker load write timeout triggered
  ↓ observable: FE log load job transitions to CANCELLED with timeout; client receives write error
```
**Trigger conditions**: S3 service degradation or network congestion; `lake_flush_thread_num_per_store` too small for write throughput.

**Break point**: Increase `lake_flush_thread_num_per_store`; investigate S3 endpoint latency; implement retry with backoff on load client.

---

### Chain 3: Leader Switch → In-flight Txns Lost → Load Job Error

```
FE leader failover or BE leader tablet epoch change
  ↓ observable: FE log leader election messages; BE log tablet leader change
In-flight publish transactions lose execution context
  ↓ observable: FE log "publish version" timeout or task not found errors
Publish phase fails; transaction cannot commit
  ↓ observable: FE log transaction publish failed; load job stuck in LOADING or transitions to CANCELLED
```
Load job fails immediately after a leader switch event.

**Trigger conditions**: FE leader re-election during active load jobs; network partition causing tablet leader re-election.

**Break point**: Retry load job after leader stabilizes; tune `transaction_publish_version_worker_num` and publish timeout.

---

### Chain 4: DataCache Checksum Mismatch → Wrong Data or BE Crash

```
DataCache block written to disk with corruption (storage fault or partial write)
  ↓ observable: BE log "checksum mismatch" during cache block read
Tablet scan reads corrupt cache block; BE validates checksum
  ↓ observable: BE log error referencing block path and expected vs actual checksum
BE returns wrong data or throws fatal exception and crashes
  ↓ observable: Query returns unexpected results OR BE crashes with signal
```
Query failure or silent data correctness issue traceable to a specific BE node.

**Trigger conditions**: Disk hardware fault; kernel page cache corruption; abnormal BE shutdown during cache flush.

**Break point**: Enable datacache checksum validation (default on); clear corrupt cache directory and allow re-population from S3; replace faulty disk.

---

## Resources

- [Shared-data architecture overview](https://docs.starrocks.io/docs/deployment/shared_data/)
