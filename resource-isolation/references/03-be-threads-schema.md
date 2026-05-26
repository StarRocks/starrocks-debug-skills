---
type: reference
category: resource-isolation
keywords: [be_threads, resource group, pip_exec, pip_scan, BOUND_CPUS, thread count comparison]
---

# 03 - BE Threads Schema Queries

---

## 7. BE_Threads - Thread Analysis

### Thread Comparison Across BEs

```sql
-- Compare thread counts across BE nodes
SELECT
    NAME,
    SUM(IF(BE_ID = 10001, 1, 0)) AS BE_10001,
    SUM(IF(BE_ID = 19097, 1, 0)) AS BE_19097,
    SUM(IF(BE_ID = 19495, 1, 0)) AS BE_19495
FROM information_schema.be_threads
GROUP BY NAME
ORDER BY NAME;
```

> **Note**: Get BE_ID from `SHOW BACKENDS` BackendId column. If one BE has significantly higher thread counts than others, investigate potential issues.

### Resource Group Threads

```sql
-- Resource group thread allocation
SELECT BE_ID, NAME, BOUND_CPUS
FROM information_schema.be_threads
WHERE NAME IN (
    'pip_exec_<resource_group_id>',
    'pip_scan_<resource_group_id>',
    'pip_con_scan_<resource_group_id>'
);
```

---