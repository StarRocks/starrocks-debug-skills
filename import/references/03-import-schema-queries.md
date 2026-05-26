---
type: reference
category: import
keywords: [loads, stream_loads, be_txns, load_tracking_logs, pipe_files, pipes, routine_load_jobs,
           FILTERED_ROWS, SINK_ROWS, zombie task, timeout, dirty data, small file risk]
---

# 03 - Import Information Schema Queries

---

## 4. Loads - Import Job Analysis

### 4.1 Failed Import Detection

```sql
-- Failed imports in last 24 hours
SELECT
    ID,
    LABEL,
    TYPE,
    STATE,
    ERROR_MSG,
    TRACKING_SQL,
    CREATE_TIME,
    LOAD_FINISH_TIME
FROM information_schema.loads
WHERE STATE = 'CANCELLED'
  AND CREATE_TIME > NOW() - INTERVAL 1 DAY
ORDER BY CREATE_TIME DESC;
```

### 4.2 Data Quality Issues

```sql
-- Tasks with filtered rows (dirty data)
SELECT
    LABEL,
    DB_NAME,
    TABLE_NAME,
    SCAN_ROWS,
    FILTERED_ROWS,
    (FILTERED_ROWS / SCAN_ROWS) * 100 AS filter_rate_percent,
    REJECTED_RECORD_PATH
FROM information_schema.loads
WHERE FILTERED_ROWS > 0
ORDER BY CREATE_TIME DESC
LIMIT 20;
```

### 4.3 Import Performance Analysis

```sql
-- Slow imports (stage-by-stage timing)
SELECT
    LABEL,
    TYPE,
    SCAN_ROWS,
    SINK_ROWS,
    TIMESTAMPDIFF(SECOND, CREATE_TIME, LOAD_START_TIME) AS queue_time_sec,
    TIMESTAMPDIFF(SECOND, LOAD_START_TIME, LOAD_COMMIT_TIME) AS load_time_sec,
    TIMESTAMPDIFF(SECOND, LOAD_COMMIT_TIME, LOAD_FINISH_TIME) AS commit_time_sec,
    (SCAN_BYTES / 1024 / 1024) / TIMESTAMPDIFF(SECOND, LOAD_START_TIME, LOAD_COMMIT_TIME) AS throughput_mb_s
FROM information_schema.loads
WHERE STATE = 'FINISHED'
  AND TIMESTAMPDIFF(SECOND, CREATE_TIME, LOAD_FINISH_TIME) > 60
ORDER BY load_time_sec DESC;
```

### 4.4 Stream Load Client Distribution

```sql
-- Stream Load client analysis (FE metadata pressure check)
SELECT
    get_json_object(RUNTIME_DETAILS, '$.client_ip') AS client_ip,
    COUNT(*) AS load_count,
    AVG(CAST(get_json_object(RUNTIME_DETAILS, '$.begin_txn_time_ms') AS SIGNED)) AS avg_begin_txn_ms,
    AVG(CAST(get_json_object(RUNTIME_DETAILS, '$.receive_data_time_ms') AS SIGNED)) AS avg_receive_ms
FROM information_schema.loads
WHERE TYPE = 'STREAM_LOAD'
  AND CREATE_TIME > NOW() - INTERVAL 1 HOUR
GROUP BY 1
ORDER BY 3 DESC;
```

### 4.5 BE-Related Import Issues

```sql
-- Imports on specific BE (backend_id = 311686287)
SELECT
    ID,
    LABEL,
    TABLE_NAME,
    RUNTIME_DETAILS
FROM information_schema.loads
WHERE CAST(RUNTIME_DETAILS AS STRING) LIKE '%311686287%'
  AND STATE = 'LOADING'
  AND CREATE_TIME > NOW() - INTERVAL 1 HOUR;
```

### 4.6 Resource Audit

```sql
-- Database write pressure (last 24 hours)
SELECT
    DB_NAME,
    COUNT(*) AS load_count,
    SUM(CASE WHEN SCAN_BYTES > 0 THEN SCAN_BYTES ELSE 0 END) / 1024 / 1024 / 1024 AS total_scan_gb,
    SUM(CASE WHEN SINK_ROWS > 0 THEN SINK_ROWS ELSE 0 END) AS total_sink_rows,
    AVG(TIMESTAMPDIFF(SECOND, CREATE_TIME, LOAD_FINISH_TIME)) AS avg_duration_sec
FROM information_schema.loads
WHERE STATE = 'FINISHED' AND CREATE_TIME > NOW() - INTERVAL 1 DAY
GROUP BY DB_NAME
ORDER BY total_scan_gb DESC;
```

### 4.7 Small File Risk Detection

```sql
-- High-frequency small imports (version explosion risk)
SELECT
    DB_NAME,
    TABLE_NAME,
    DATE_FORMAT(CREATE_TIME, '%Y-%m-%d %H:%i') AS minute_slot,
    COUNT(*) AS load_per_minute,
    AVG(SCAN_BYTES) / 1024 AS avg_load_kb
FROM information_schema.loads
WHERE TYPE = 'STREAM_LOAD' AND CREATE_TIME > NOW() - INTERVAL 1 HOUR
GROUP BY 1, 2, 3
HAVING load_per_minute > 10
ORDER BY load_per_minute DESC;
```

**Recommendation**: If avg_load_kb is only tens of KB with high frequency, increase Flink Sink `buffer_flush_interval` or `batch_size`.

### 4.8 Zombie Task Detection

```sql
-- Tasks running > 30 minutes without completion
SELECT
    ID,
    LABEL,
    USER,
    STATE,
    TYPE,
    TIMESTAMPDIFF(MINUTE, CREATE_TIME, NOW()) AS running_minutes,
    get_json_object(RUNTIME_DETAILS, '$.txn_id') AS txn_id,
    get_json_object(PROPERTIES, '$.timeout') AS config_timeout
FROM information_schema.loads
WHERE STATE NOT IN ('FINISHED', 'CANCELLED')
  AND CREATE_TIME < NOW() - INTERVAL 30 MINUTE;
```

**Action**: Cancel zombie tasks with `CANCEL LOAD FROM db_name WHERE LABEL = "xxx";`

### 4.9 Data Skew Detection

```sql
-- BE distribution for specific large job
SELECT
    ID,
    LABEL,
    TABLE_NAME,
    get_json_object(RUNTIME_DETAILS, '$.backends') AS participating_backends,
    get_json_object(RUNTIME_DETAILS, '$.unfinished_backends') AS stuck_backends
FROM information_schema.loads
WHERE ID = 1482344928;  -- Replace with your Job ID
```

### 4.10 Timeout Analysis

```sql
-- Timeout tasks with progress analysis
SELECT
    LABEL,
    SCAN_ROWS,
    SCAN_BYTES / 1024 / 1024 AS scan_mb,
    CAST(get_json_object(PROPERTIES, '$.timeout') AS SIGNED) AS config_timeout_sec,
    TIMESTAMPDIFF(SECOND, CREATE_TIME, LOAD_FINISH_TIME) AS actual_duration_sec,
    ERROR_MSG
FROM information_schema.loads
WHERE STATE = 'CANCELLED'
  AND (ERROR_MSG LIKE '%timeout%' OR ERROR_MSG LIKE '%Timeout%')
ORDER BY CREATE_TIME DESC;
```

### 4.11 Dirty Data Query Generation

```sql
-- Generate dirty data debug SQL
SELECT
    CONCAT('/* Dirty data: ', LABEL, ' */ ', TRACKING_SQL) AS debug_sql
FROM information_schema.loads
WHERE FILTERED_ROWS > 0
  AND TRACKING_SQL IS NOT NULL
ORDER BY CREATE_TIME DESC
LIMIT 5;
```

---

---

## 5. Stream_Loads - Real-time Import Monitoring

```sql
-- Stream Load table statistics (last 1 hour)
SELECT
    DB_NAME,
    TABLE_NAME,
    COUNT(*) AS load_count,
    SUM(NUM_LOAD_BYTES) / 1024 / 1024 AS total_mb,
    AVG(TIMEOUT_SECOND) AS avg_timeout,
    AVG(END_TIME_MS - START_LOADING_TIME_MS) / 1000 AS avg_duration_sec
FROM information_schema.stream_loads
WHERE STATE = 'FINISHED'
  AND CREATE_TIME_MS > (UNIX_TIMESTAMP(NOW() - INTERVAL 1 HOUR) * 1000)
GROUP BY DB_NAME, TABLE_NAME
ORDER BY total_mb DESC;
```

---

---

## 8. BE_Txns - Large Import Detection

```sql
-- Large imports within time range
SELECT
    SUM(NUM_ROW) AS load_rows,
    SUM(DATA_SIZE) AS total_size,
    TXN_ID
FROM be_txns
WHERE PUBLISH_TIME BETWEEN
    UNIX_TIMESTAMP('2025-01-04 09:00:00') AND
    UNIX_TIMESTAMP('2025-01-04 10:00:00')
GROUP BY TXN_ID
ORDER BY total_size;
```

---

---

## 14. Load_Tracking_Logs - Import Error Details

> Available since v3.0.

```sql
-- Query by label (use JOB_ID or LABEL from loads view)
SELECT * FROM information_schema.load_tracking_logs
WHERE label = 'user_behavior'\G
```

---

---

## 15. Pipe_Files & Pipes - Pipe Import Status

> Available since v3.2.

```sql
-- Pipe file import status
SELECT * FROM information_schema.pipe_files;

-- Pipe details
SELECT * FROM information_schema.pipes;

-- Alternative command
SHOW PIPES;
```

---

---

## 16. Routine_Load_Jobs - Routine Import Monitoring

```sql
SELECT * FROM information_schema.routine_load_jobs;
```

---