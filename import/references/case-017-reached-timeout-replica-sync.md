---
type: reference
category: import
issue: reached-timeout
keywords: [routine load, reached timeout, segment_replicate, flush_thread_num_per_store, replica sync]
---

# Case-017: Reached Timeout Due to Replica Sync Bottleneck

## Environment

- StarRocks version: 2.5.1
- Architecture: shared-nothing
- Cluster scale: 2000+ Routine Load jobs
- Hardware: Many disks, high CPU core count

## Symptom

Cluster with 2000+ Routine Load jobs experienced frequent `Reached timeout` errors across multiple tables during 7:00-8:00 AM.

## Investigation

### Step 1: Determine Problem Scope

Aggregate timeout transactions by minute during the incident window:

```bash
grep -a -E "2025-10-22 07|2025-10-22 08" fe.log.20251022-* | \
  grep -a abortTransaction | grep "Reached timeout" | \
  awk -vOFS=":" -F ':' '{print $2,$3}' | sort | uniq -c | sort -rn
```

Output:
```
   560 2025-10-22 08:59
   524 2025-10-22 07:03
   137 2025-10-22 08:58
   112 2025-10-22 07:04
    39 2025-10-22 07:45
    ...
```

Shows concentrated failures at specific minutes, indicating burst bottleneck.

### Step 2: Check Monitoring

Observations from Grafana:
- `segment_replicate_sync` thread pool: `active` ≈ `total`, persistent `queue` backlog
- Network traffic: moderate, no saturation
- Disk IO: moderate utilization
- BRPC latency: P99 elevated for `tablet_writer_add_segment`

### Step 3: Trace Failed Transaction Logs

On BE node xxx.xxx.xx.71, search for txn_id=2127568939:

```bash
grep 2127568939 be.INFO
```

Key findings:

```
I20251022 07:45:27 local_tablets_channel.cpp:766] LocalTabletsChannel txn_id: 2127568939 open 2 delta writer

W20251022 07:46:27 segment_replicate_executor.cpp:162] Failed to send rpc to SyncChannnel
  [host: xxx.xxx.xx.65, port: 8060, tablet_id: 1314735075, txn_id: 2127568939]
  err=Internal error: [E110]Fail to connect Socket Connection timed out
  [R1][E1008]Reached timeout=30000ms @xxx.xxx.xx.65:8060
```

The 30-second timeout hit during segment sync to secondary replica at xxx.xxx.xx.65.

### Step 4: Trace Secondary Replica Side

On BE xxx.xxx.xx.65, search related logs:

```
I20251022 07:46:12 local_tablets_channel.cpp:810] cancel LocalTabletsChannel txn_id: 2127568786
  reason: primary replica on host [xxx2] failed to sync data to secondary replica

I20251022 07:47:18 local_tablets_channel.cpp:353] LocalTabletsChannel txn_id: 2127568786
  wait tablet 5217727969 secondary replica finish already 65272ms still in state 1
```

Secondary replica processing was slow, causing primary replica sync to timeout.

## Root Cause

With 2000+ Routine Load jobs, the burst load during peak hours saturated the `segment_replicate_sync` thread pool. The default `flush_thread_num_per_store=2` per disk was insufficient for the high replica sync workload, causing:

1. Thread pool queue buildup
2. Secondary replica processing delays
3. Primary replica sync timeout (30-second BRPC timeout)

## Resolution

### Short-term

Increase flush thread pool size:

```bash
# BE config (dynamic, no restart required)
flush_thread_num_per_store = 6
```

Continue monitoring after adjustment.

### Long-term

- Tune thread pool sizes based on cluster scale: `flush_thread_num_per_store` should scale with disk count and import concurrency
- For clusters with many disks and high CPU cores, consider 4-6 threads per store
- Monitor `segment_replicate_sync` pool metrics for early warning

## Key Metrics

| Metric | What to Watch |
|---|---|
| `segment_replicate_sync.active` / `total` | Pool saturation |
| `segment_replicate_sync.queue` | Task backlog |
| `segment_replicate_sync.pending` | Queue wait time |
| BRPC `tablet_writer_add_segment latency-99` | Replica sync latency |

## Lessons Learned

- High Routine Load concurrency can saturate replica sync threads
- The `segment_replicate_executor.cpp:162` log entry is key evidence for replica sync timeout
- Thread pool tuning should consider cluster scale (#disks, #CPU cores, import job count)
- Default `flush_thread_num_per_store=2` may be too conservative for large clusters

## Related Skills

- [02-import.md](../../[SKILL.md](../SKILL.md)) — Write-slow diagnosis, thread pool monitoring
- Section 5: Segment Replicate Sync Monitoring

---

## Related Cases

- case-001-broker-load-backlog — Thread pool saturation (async_load_task_pool)
- case-002-rpc-failed-statistics — RPC bottleneck from statistics collection