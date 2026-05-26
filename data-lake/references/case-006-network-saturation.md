---
type: reference
category: data-lake
issue: network-saturation
keywords: [HMS, Hive Metastore, network saturation, jstack, external table, shuffle]
---

# Case-006: CN Network Bandwidth Saturation (HMS Blocking)

## Environment

- StarRocks version: 3.2.16
- Architecture: shared-data

## Symptom

A specific CN node's network was continuously saturated.

## Investigation

1. Analyzed workload: heavy Hive Catalog queries plus normal imports.
2. Ruled out large result sets (confirmed via audit logs).
3. Discovered users running many JOINs between internal tables and Hive tables.
4. HMS metadata service was experiencing issues, causing execution plan nodes to block on
   network I/O.
5. **jstack confirmed**:
   ```
   "starrocks-mysql-nio-pool-646" ... RUNNABLE
     at java.net.SocketInputStream.socketRead0(Native Method)
     at org.apache.thrift.transport.TIOStreamTransport.read(...)
     at org.apache.hadoop.hive.metastore.security.TFilterTransport.readAll(...)
   ```

## Root Cause

HMS service degradation caused Thrift connections to block, generating massive shuffle
intermediate data that saturated network bandwidth.

## Resolution

- Fix the HMS service.
- Add timeout controls for HMS access so a slow metastore can't generate unbounded shuffle.

## Lessons Learned

- Network saturation on a single node with no obvious large result set strongly suggests
  upstream metadata service issues.
- For external-table workloads, HMS health is part of the cluster's critical path —
  monitor it the same way you monitor FE/BE.

## Related Skills

- [SKILL.md](../SKILL.md) — Hive Metastore connection issues and network-layer bottlenecks
