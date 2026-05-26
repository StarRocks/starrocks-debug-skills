---
type: reference
category: tablet
keywords: [schema change, ALTER TABLE COLUMN, SHOW ALTER, SHOW TABLET, replica status, ADMIN SET REPLICA STATUS, VersionCount]
---

# 02 - Schema Change & Tablet Management Commands

---

## 7. Schema Change Troubleshooting

```sql
-- Check ongoing schema change
SHOW ALTER TABLE COLUMN WHERE TableName = "<table>" ORDER BY CreateTime DESC LIMIT 1;

-- Check materialized view creation progress
SHOW ALTER MATERIALIZED VIEW FROM <db_name>;

-- Cancel MV creation
CANCEL ALTER MATERIALIZED VIEW FROM <db_name>.<view_name>;

-- Check table status (NORMAL / SCHEMA_CHANGE)
SHOW PROC "/dbs/<db_id>";
```

Schema change failures: search BE logs for `failed to process the version`,
`failed to process the schema change`, `fail to execute schema change`,
`fail to convert rowset`, or `Fail to link`.

Speed up schema change: increase `alter_tablet_worker_count` (default 3),
increase `memory_limitation_per_thread_for_schema_change` (default 2G).

---

---

## 9. Tablet Management

```sql
-- View tablet distribution
SHOW TABLET FROM <table_name>;
SHOW TABLET <tablet_id>;

-- Check inconsistent replicas
SHOW PROC "/statistic/<db_id>";

-- View tablet compaction history
SHOW PROC '/cluster_balance/history_tablets';

-- Mark tablet as bad (triggers re-replication)
ADMIN SET REPLICA STATUS PROPERTIES("tablet_id" = "<id>", "backend_id" = "<be_id>", "status" = "bad");
```

Replica health: `SHOW TABLET` and compare `VERSION`, `LstFailedVersion`, `LstSuccessVersion`
across replicas. `VersionCount` greater than 500 continuously means compaction cannot keep up.

---