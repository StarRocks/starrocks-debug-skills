---
type: reference
category: materialized-view
keywords: [task_runs, tasks, PENDING, RUNNING, FAILED, SUCCESS, task_name]
---

# 05 - Task & Task_Runs Schema Queries

---

## 13. Tasks & Task_Runs - ETL Task Monitoring

```sql
-- List all tasks
SELECT * FROM INFORMATION_SCHEMA.tasks;
SELECT * FROM information_schema.tasks WHERE task_name = '<task_name>';

-- List task runs with status
SELECT * FROM INFORMATION_SCHEMA.task_runs;
SELECT * FROM information_schema.task_runs WHERE task_name = '<task_name>';
```

### TaskRun States

| State | Description |
|---|---|
| PENDING | Waiting to execute |
| RUNNING | Executing |
| FAILED | Execution failed |
| SUCCESS | Execution successful |

---