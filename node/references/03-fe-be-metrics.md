---
type: reference
category: node
keywords: [fe_metrics, be_metrics, fe_memory_usage, meta_log_count, mem_bytes, malloc, sys schema]
---

# 03 - FE/BE Metrics & Memory Schema Queries

---

## 11. FE_Metrics - FE Monitoring

```sql
-- Edit Log count per FE
SELECT
    NAME,
    SUM(IF(FE_ID = '172.26.92.154_19010_1716174646625', VALUE, NULL)) AS FE_154,
    SUM(IF(FE_ID = '172.26.194.184_19010_1765939312252', VALUE, NULL)) AS FE_184,
    SUM(IF(FE_ID = '172.26.92.155_19010_1762244987643', VALUE, NULL)) AS FE_155
FROM fe_metrics
WHERE NAME = 'meta_log_count'
GROUP BY NAME
ORDER BY NAME;
```

---

---

## 12. BE_Metrics - Memory Usage

```sql
-- Memory metrics per BE
SELECT
    NAME,
    SUM(IF(BE_ID = 10001, VALUE, NULL)) AS BE_10001,
    SUM(IF(BE_ID = 19097, VALUE, NULL)) AS BE_19097,
    SUM(IF(BE_ID = 19495, VALUE, NULL)) AS BE_19495
FROM be_metrics
WHERE NAME LIKE '%mem_bytes'
   OR NAME LIKE '%malloc%'
GROUP BY NAME
ORDER BY NAME;
```

---

---

## 19. FE_Memory_Usage - FE Memory Breakdown

```sql
USE sys;
SELECT * FROM fe_memory_usage;
```

> **Note**: Absolute values may not be 100% accurate, but useful for relative comparison.

---

## Quick Reference by Scenario

| Scenario | Key Tables |
|---|---|
| Import failure | `loads`, `load_tracking_logs`, `stream_loads` |
| Data quality | `loads` (FILTERED_ROWS) |
| Performance bottleneck | `loads`, `be_metrics`, `fe_metrics`, `be_threads` |
| Tablet health | `be_tablets`, `partitions_meta`, `be_compactions` |
| Compaction issues | `be_compactions`, `be_cloud_native_compactions` |
| MV refresh | `materialized_views`, `task_runs` |
| Data Cache | `be_datacache_metrics` |
| Permissions/RBAC | `sys.grants_to_roles`, `sys.grants_to_users`, `sys.role_edges` |
| Statistics | `_statistics_.table_statistic_v1` |

---

## Usage

This document provides ready-to-use SQL queries for common diagnostic scenarios. Combine with:

- `tools/01-diagnostic-commands.md` for shell commands and advanced MV queries.
- `skills/01-query.md` for query-specific troubleshooting.
- `skills/02-import.md` for import pipeline issues.
- `skills/03-node.md` for BE/FE node problems.