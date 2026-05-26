---
type: reference
category: shared-data
issue: datacache-autoscaling
keywords: [DataCache, autoscaling, compaction score, S3 IOPS, query latency, ingestion warehouse]
---

# Case-013: DataCache Autoscaling Causing Compaction Regression

## Environment

- StarRocks version: 3.4.0
- Architecture: shared-data, 120 BE (ingestion) + 80 BE (query) + 10 BE (compaction)

## Symptom

After approximately 18 hours of stable 90K events/s ingestion, query P99 latency suddenly
spiked. Compaction score rose sharply.

## Investigation

1. **Correlation**: both query latency and compaction score began increasing at the same timestamp.
2. **Compaction analysis**: `information_schema.be_cloud_native_compactions` showed compaction
   tasks spending excessive time reading remote data. S3 read IOPS suddenly spiked from
   ingestion warehouse BEs.
3. **Per-node analysis**: chose the first BE whose S3 read IOPS spiked. Found DataCache
   autoscaling warning logs in `be.WARNING` at that exact timestamp.
4. **Pattern confirmation**: every time disk utilization reached 80%, DataCache shrank,
   S3 GET IOPS spiked, compaction slowed, compaction score rose, query latency increased.
5. **Scope**: 120 BEs' autoscaling events were not time-aligned, and autoscaling repeated
   on each BE — compaction always had cache misses for realtime ingested data.

## Root Cause

DataCache autoscaling evicted hot data when disk utilization hit the threshold. In a pure
shared-data setup with separate ingestion/query warehouses, there is no disk resource
competition, so autoscaling is unnecessary and harmful.

## Resolution

- Disable DataCache autoscaling for ingestion warehouses.
- Other optimizations applied:
  - Increased ingestion-client CPU (which was the actual ingestion bottleneck).
  - Put compaction and ingestion in the same warehouse.
  - Used `num_partitioned_prefix = 100` for S3 request distribution.
  - Disabled global profile collection; used `big_query_profile_threshold` instead.
- Result: compaction score maintained below 100, P99 query latency around 300 ms.

## Lessons Learned

- Compaction-score climbs during steady-state ingestion are usually a cache-locality
  problem, not a compaction-throughput problem.
- Default-on autoscaling assumes contended disks. In dedicated-warehouse architectures,
  it can be net-negative.

## Related Skills

- [SKILL.md](../SKILL.md) — DataCache autoscaling regression and compaction-score management
