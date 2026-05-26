---
type: reference
category: import
issue: rpc-failed
keywords: [RPC failed, INSERT INTO SELECT, statistics collection, brpc, DELETE, VisibleVersion]
---

# Case-002: RPC Failed Causing Batch Import Failures (Statistics Starving RPC)

## Environment

- StarRocks version: 3.2.11
- Architecture: shared-data, 3 FE + 18 CN co-located, 96 cores / 512-768 GB

## Symptom

- Nightly batch `INSERT INTO SELECT` tasks failed with `rpc failed`.
- Stream Load worked fine — only `INSERT INTO SELECT` was affected.
- BI front-end queries also slowed down (from under 10s to 1-2 minutes).

## Investigation

1. **Monitoring review**: machine load, import queues, fslib, starlet dashboards — no obvious anomalies.
2. **pstack capture**: multiple captures on error-reporting CN nodes; one node showed
   `FlushToken`-related stacks.
3. **Profile analysis**: `deployWaitTime` was extremely high; instance dispatch intervals on BE were very long.
4. **jstack analysis**: found statistics collection stacks running for extended periods.
5. **FE log analysis**: full statistics collection grew from 2+ hours to 17+ hours.
6. **brpc metrics check**:
   ```bash
   curl -s http://<cn_ip>:8060/vars | grep exec_
   ```
   P99 latency grew from 35s -> 58s -> 81s continuously.
7. **Network layer check**:
   ```bash
   netstat -na | grep 8060
   ```
   Found `recv-Q` and `send-Q` with 1M+ backlog between nodes.

## Root Cause

- The application used a `DELETE + INSERT` pattern for T+1 imports.
- `DELETE` operations update `VisibleVersion` on **all** partitions.
- This triggered statistics collection on a massive number of partitions (6K+ tasks daily).
- Statistics collection consumed RPC resources, starving normal FE-to-BE communication.

## Resolution

### Short-term mitigation

```sql
-- Disable statistics collection
ADMIN SET FRONTEND CONFIG("enable_collect_full_statistic"="false");
ADMIN SET FRONTEND CONFIG("enable_statistic_collect"="false");
ADMIN SET FRONTEND CONFIG("enable_statistic_collect_on_first_load"="false");

-- Increase RPC timeout
ADMIN SET FRONTEND CONFIG("brpc_send_plan_fragment_timeout_ms"="180000");

-- Reduce statistics collection concurrency
ADMIN SET FRONTEND CONFIG("statistic_collect_concurrency"="1");
```

### Long-term

- Optimize statistics collection: skip collection when `VERSION` changes but data volume hasn't significantly changed.
- Fix `DELETE` to stop updating `VisibleVersion` on all partitions.
- Application-side: use `TRUNCATE PARTITION` instead of `DELETE` (doesn't update all partition versions).

### Validation commands

```sql
-- Check partition version timestamps
SELECT partition_name, COMPACT_VERSION, VISIBLE_VERSION, VISIBLE_VERSION_TIME
FROM information_schema.partitions_meta WHERE table_name = '<table>';

-- Check statistics collection history
SHOW ANALYZE STATUS;
```

## Lessons Learned

- A slow background process (statistics) can starve foreground RPC and look exactly like a
  network problem.
- Always correlate brpc P99 trends with statistics-collection workload before blaming the network.
- `DELETE`-based T+1 patterns scale badly because they touch every partition's `VisibleVersion`.

## Related Skills

- [SKILL.md](../SKILL.md) — RPC Failed diagnosis section
- `skills/10-resource-isolation.md` — emergency statistics-collection disable pattern
