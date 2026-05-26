---
type: reference
category: materialized-view
keywords: [SHOW MATERIALIZED VIEWS, task_runs, RUNNING, CANCEL ALTER, SHOW ALTER MATERIALIZED VIEW]
---

# 04 - Materialized View Diagnostic Commands

---

## 12. Materialized View Diagnostics

For comprehensive MV diagnostic SQL queries, see [tools/03-mv-diagnostic-sql.md](03-mv-diagnostic-sql.md).

Quick reference:

```sql
-- Check MV state
SHOW MATERIALIZED VIEWS;

-- View refresh history
SELECT * FROM information_schema.task_runs WHERE task_name = 'mv-<mv_id>' \G

-- Find currently RUNNING MV tasks
SELECT TASK_NAME, CREATE_TIME, FINISH_TIME, STATE 
FROM information_schema.task_runs WHERE STATE = 'RUNNING';
```

---