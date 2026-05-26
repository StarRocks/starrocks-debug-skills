---
type: reference
category: mv-diagnostic-sql
keywords: [materialized view, MV, task_runs, refresh, query rewrite, inactive, partition]
---

# 03 - Materialized View Diagnostic SQL

A collection of SQL queries for diagnosing async materialized view issues.

---

## 1. Basic MV Status Queries

```sql
-- 1. Find recent 50 async MV task runs
SELECT 
    t.TASK_NAME,
    m.TABLE_SCHEMA,
    m.TABLE_NAME AS MATERIALIZED_VIEW_NAME,
    t.CREATE_TIME,
    t.FINISH_TIME,
    TIMESTAMPDIFF(SECOND, t.CREATE_TIME, t.FINISH_TIME) AS RUNNING_TIME_SECONDS,
    t.STATE
FROM 
    information_schema.task_runs t
    LEFT JOIN information_schema.materialized_views m ON t.TASK_NAME = m.TASK_NAME
ORDER BY 
    t.CREATE_TIME DESC
LIMIT 50;

-- 2. Find MV tasks in MERGED or PENDING state
SELECT 
    t.TASK_NAME,
    m.TABLE_SCHEMA,
    m.TABLE_NAME AS MATERIALIZED_VIEW_NAME,
    t.CREATE_TIME,
    t.FINISH_TIME,
    TIMESTAMPDIFF(SECOND, t.CREATE_TIME, t.FINISH_TIME) AS RUNNING_TIME_SECONDS,
    t.STATE
FROM 
    information_schema.task_runs t
    LEFT JOIN information_schema.materialized_views m ON t.TASK_NAME = m.TASK_NAME
WHERE 
    t.STATE IN ('MERGED', 'PENDING')
    AND t.CREATE_TIME IS NOT NULL
ORDER BY 
    t.CREATE_TIME DESC
LIMIT 50;

-- 3. Find inactive or failed MVs
SELECT 
    m.TABLE_SCHEMA,
    m.TABLE_NAME AS MATERIALIZED_VIEW_NAME,
    m.TASK_NAME,
    m.IS_ACTIVE,
    m.INACTIVE_REASON,
    m.LAST_REFRESH_STATE,
    m.LAST_REFRESH_ERROR_MESSAGE,
    t.STATE AS TASK_STATE,
    t.ERROR_MESSAGE AS TASK_ERROR_MESSAGE,
    t.CREATE_TIME,
    t.FINISH_TIME,
    TIMESTAMPDIFF(SECOND, t.CREATE_TIME, t.FINISH_TIME) AS RUNNING_TIME_SECONDS
FROM 
    information_schema.materialized_views m
    LEFT JOIN information_schema.task_runs t ON m.TASK_NAME = t.TASK_NAME
WHERE 
    (m.IS_ACTIVE = 'false' 
     OR t.STATE = 'FAILED')
    AND (t.CREATE_TIME IS NULL OR t.CREATE_TIME >= NOW() - INTERVAL 24 HOUR)
ORDER BY 
    t.CREATE_TIME DESC
LIMIT 50;

-- 4. Find MVs refreshing too many partitions
SELECT 
    t.TASK_NAME,
    m.TABLE_SCHEMA,
    m.TABLE_NAME AS MATERIALIZED_VIEW_NAME,
    m.LAST_REFRESH_MV_REFRESH_PARTITIONS,
    TIMESTAMPDIFF(SECOND, t.CREATE_TIME, t.FINISH_TIME) AS RUNNING_TIME_SECONDS,
    t.STATE
FROM 
    information_schema.task_runs t
    LEFT JOIN information_schema.materialized_views m ON t.TASK_NAME = m.TASK_NAME
WHERE 
    t.CREATE_TIME >= NOW() - INTERVAL 24 HOUR
    AND LENGTH(m.LAST_REFRESH_MV_REFRESH_PARTITIONS) > 10
ORDER BY 
    t.CREATE_TIME DESC
LIMIT 50;

-- 5. Find MVs with refresh time > 1 minute
SELECT 
    t.TASK_NAME,
    m.TABLE_SCHEMA,
    m.TABLE_NAME AS MATERIALIZED_VIEW_NAME,
    t.CREATE_TIME,
    t.FINISH_TIME,
    TIMESTAMPDIFF(SECOND, t.CREATE_TIME, t.FINISH_TIME) AS RUNNING_TIME_SECONDS,
    t.STATE
FROM 
    information_schema.task_runs t
    LEFT JOIN information_schema.materialized_views m ON t.TASK_NAME = m.TASK_NAME
WHERE 
    t.CREATE_TIME >= NOW() - INTERVAL 24 HOUR
    AND TIMESTAMPDIFF(SECOND, t.CREATE_TIME, t.FINISH_TIME) > 60
ORDER BY 
    RUNNING_TIME_SECONDS DESC
LIMIT 50;

-- 6. Find unpartitioned MVs with large data (> 50M rows)
SELECT 
    m.TASK_NAME, 
    m.TABLE_SCHEMA,
    m.TABLE_NAME AS MATERIALIZED_VIEW_NAME,
    m.PARTITION_TYPE,
    m.TABLE_ROWS,
    t.STATE
FROM 
    information_schema.materialized_views m
    LEFT JOIN information_schema.task_runs t ON m.TASK_NAME = t.TASK_NAME
WHERE 
    m.PARTITION_TYPE = 'UNPARTITIONED'
    AND m.TABLE_ROWS > 50000000
ORDER BY 
    m.TABLE_ROWS DESC
LIMIT 50;

-- 7. List all active async MVs
SELECT 
    DISTINCT m.TABLE_SCHEMA,
    m.TABLE_NAME AS MATERIALIZED_VIEW_NAME,
    m.TASK_NAME,
    m.REFRESH_TYPE
FROM 
    information_schema.materialized_views m
WHERE 
    m.IS_ACTIVE='true'
ORDER BY 
    m.TABLE_SCHEMA, m.TABLE_NAME
LIMIT 50;

-- 8. Batch modify session variables for active MVs (e.g., pipeline_dop)
SELECT 
    CONCAT(
        'ALTER MATERIALIZED VIEW ',
        m.TABLE_SCHEMA, '.', m.TABLE_NAME,
        ' SET (''session.pipeline_dop''=''1'');'
    ) AS ALTER_STATEMENT
FROM 
    information_schema.materialized_views m
WHERE 
    m.IS_ACTIVE = 'true'
GROUP BY 
    m.TABLE_SCHEMA, m.TABLE_NAME;

-- 9. Find currently RUNNING MV tasks
SELECT 
    TASK_NAME,
    CREATE_TIME,
    FINISH_TIME,
    STATE
FROM 
    information_schema.task_runs
WHERE 
    STATE = 'RUNNING';
```

---

## 2. MV Performance Analysis

```sql
-- 10. Find actual MV refresh duration (successful runs)
WITH calculated_runs AS (
    SELECT
        TASK_NAME,
        `DATABASE`,
        get_json_string(PROPERTIES, '$.mvId') AS mvId,
        left(DEFINITION, 50) AS definition_preview,
        greatest(
            ((unix_timestamp(FINISH_TIME) * 1000) - get_json_double(EXTRA_MESSAGE, '$.processStartTime')) / 1000,
            1
        ) AS duration_sec,
        FINISH_TIME
    FROM
        information_schema.task_runs
    WHERE
        STATE = 'SUCCESS'
),
prepared_data AS (
    SELECT
        TASK_NAME,
        `DATABASE`,
        mvId,
        definition_preview,
        duration_sec,
        FINISH_TIME,
        ROW_NUMBER() OVER(PARTITION BY TASK_NAME ORDER BY FINISH_TIME DESC) as rn_last_run,
        ROW_NUMBER() OVER(PARTITION BY TASK_NAME ORDER BY duration_sec DESC, FINISH_TIME DESC) as rn_longest_run
    FROM
        calculated_runs
)
SELECT
    TASK_NAME,
    MIN(`DATABASE`) AS `DATABASE`,
    MIN(mvId) AS mvId,
    MIN(definition_preview) AS definition_preview,
    MIN(duration_sec) AS min_run_time_sec,
    MAX(duration_sec) AS max_run_time_sec,
    MAX(CASE WHEN rn_longest_run = 1 THEN FINISH_TIME ELSE NULL END) AS max_run_time_corresponding_finish_time,
    MAX(CASE WHEN rn_last_run = 1 THEN duration_sec ELSE NULL END) AS last_run_duration_sec,
    MAX(FINISH_TIME) AS last_run_time
FROM
    prepared_data
GROUP BY
    TASK_NAME;

-- 11. Find failed MV refresh details
WITH failed_tasks AS (
    SELECT
        TASK_NAME,
        `DATABASE`,
        get_json_string(PROPERTIES, '$.mvId') AS mvId,
        left(DEFINITION, 50) AS definition_preview,
        FINISH_TIME,
        greatest(
            ((unix_timestamp(FINISH_TIME) * 1000) - get_json_double(EXTRA_MESSAGE, '$.processStartTime')) / 1000,
            1
        ) AS duration_sec,
        ERROR_MESSAGE
    FROM
        information_schema.task_runs
    WHERE
        STATE = 'FAILED'
),
ranked_failures AS (
    SELECT
        TASK_NAME,
        `DATABASE`,
        mvId,
        definition_preview,
        FINISH_TIME,
        duration_sec,
        ERROR_MESSAGE,
        ROW_NUMBER() OVER(PARTITION BY TASK_NAME, ERROR_MESSAGE ORDER BY duration_sec DESC) as rn
    FROM
        failed_tasks
)
SELECT
    TASK_NAME,
    `DATABASE`,
    mvId,
    definition_preview,
    FINISH_TIME,
    duration_sec,
    ERROR_MESSAGE
FROM
    ranked_failures
WHERE
    rn = 1;
```

---

## 3. MV Query Rewrite Analysis

```sql
-- 12. Find MVs frequently hitting query rewrite
SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    IS_ACTIVE,
    INACTIVE_REASON,
    TASK_ID,
    TASK_NAME,
    LAST_REFRESH_START_TIME,
    LAST_REFRESH_FINISHED_TIME,
    LAST_REFRESH_DURATION,
    LAST_REFRESH_STATE,
    LAST_REFRESH_ERROR_CODE,
    LAST_REFRESH_ERROR_MESSAGE,
    EXTRA_MESSAGE,
    QUERY_REWRITE_STATUS
FROM information_schema.materialized_views
WHERE TABLE_NAME IN (
    SELECT
        hitMvs
    FROM starrocks_audit_db__.starrocks_audit_tbl__
    WHERE `timestamp` >= NOW() - INTERVAL 1 DAY
      AND hitMvs != 'null'
      AND hitMvs != ''
);

-- 13. Find MVs NOT hitting query rewrite
SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    IS_ACTIVE,
    INACTIVE_REASON,
    TASK_ID,
    TASK_NAME,
    LAST_REFRESH_START_TIME,
    LAST_REFRESH_FINISHED_TIME,
    LAST_REFRESH_DURATION,
    LAST_REFRESH_STATE,
    LAST_REFRESH_ERROR_CODE,
    LAST_REFRESH_ERROR_MESSAGE,
    EXTRA_MESSAGE,
    QUERY_REWRITE_STATUS
FROM information_schema.materialized_views
WHERE TABLE_NAME NOT IN (
    SELECT
        hitMvs
    FROM starrocks_audit_db__.starrocks_audit_tbl__
    WHERE `timestamp` >= NOW() - INTERVAL 1 DAY
      AND hitMvs != 'null'
      AND hitMvs != ''
);
```

---

## Usage

Use these queries alongside:

- **skills/04-materialized-view.md** — for troubleshooting context and root cause analysis
- **tools/01-diagnostic-commands.md** — for general diagnostic commands (profile, logs, etc.)

Note: Queries 12 and 13 require the audit log table `starrocks_audit_db__.starrocks_audit_tbl__` to be configured.