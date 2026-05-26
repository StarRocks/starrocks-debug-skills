---
type: reference
category: tablet
keywords: [tables, tables_config, partitions_meta, be_tablets, DATA_LENGTH, bloom_filter_columns,
           NUM_ROWSET, NUM_SEGMENT, NUM_VERSION, data_size_gb, STATE, abnormal replica]
---

# 03 - Tablet & Partition Information Schema Queries

---

## 1. Tables - Capacity Analysis

### Internal Tables

```sql
-- Database data size (GB)
SELECT
    TABLE_SCHEMA AS dbname,
    ROUND(SUM(DATA_LENGTH)/1024/1024/1024, 3) AS datasize
FROM information_schema.tables
GROUP BY TABLE_SCHEMA;

-- Database row count
SELECT
    TABLE_SCHEMA AS dbname,
    SUM(TABLE_ROWS) AS table_rows
FROM information_schema.tables
GROUP BY TABLE_SCHEMA;

-- Top 3 largest tables per database
WITH ranked_tables AS (
    SELECT
        TABLE_SCHEMA,
        TABLE_NAME,
        DATA_LENGTH,
        ROUND(DATA_LENGTH / 1024 / 1024 / 1024, 2) AS DATA_LENGTH_GB,
        ROW_NUMBER() OVER (
            PARTITION BY TABLE_SCHEMA
            ORDER BY DATA_LENGTH DESC
        ) AS rn
    FROM INFORMATION_SCHEMA.TABLES
)
SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    DATA_LENGTH_GB
FROM ranked_tables
WHERE rn <= 3
ORDER BY TABLE_SCHEMA, DATA_LENGTH_GB DESC;
```

### External Tables

```sql
-- External catalog tables (limited info available)
SHOW CATALOGS;
SET CATALOG xxx;
SELECT * FROM information_schema.tables;
-- Note: External tables may show NULL for many fields
```

---

---

## 2. Tables_Config - Index Configuration

```sql
-- Find tables with Bloom Filter index
SELECT
    table_schema,
    table_name,
    table_model,
    primary_key,
    partition_key,
    distribute_key,
    sort_key,
    regexp_extract(properties, '"bloom_filter_columns":"([^"]+)"', 1) AS bloom_cols
FROM information_schema.tables_config
WHERE properties LIKE '%"bloom_filter_columns"%';
```

---

---

## 10. Partitions_Meta & BE_Tablets - Tablet Health

### Rowset/Segment Overflow Detection

```sql
-- Tables with too many versions or segments
SELECT
    pm.DB_NAME,
    pm.TABLE_NAME,
    tbt.TABLET_ID,
    tbt.NUM_VERSION,
    tbt.NUM_SEGMENT,
    tbt.DATA_SIZE / (1024 * 1024 * 1024) AS data_size_gb
FROM information_schema.partitions_meta pm
JOIN information_schema.be_tablets tbt ON pm.PARTITION_ID = tbt.PARTITION_ID
WHERE tbt.NUM_ROWSET > 100
   OR tbt.NUM_SEGMENT > 50
ORDER BY tbt.NUM_VERSION DESC, tbt.NUM_SEGMENT DESC
LIMIT 10;
```

> **Impact**: Too many versions/segments increase scan overhead and reduce query efficiency.
> **Solution**: Reduce import frequency or trigger compaction via `ALTER TABLE ... COMPACT`.

### Abnormal Replica Detection

```sql
-- Tablets with abnormal replica state
SELECT
    pm.DB_NAME,
    pm.TABLE_NAME,
    tbt.TABLET_ID,
    tbt.BE_ID,
    tbt.STATE,
    tbt.DATA_SIZE / (1024 * 1024 * 1024) AS data_size_gb
FROM information_schema.partitions_meta pm
JOIN information_schema.be_tablets tbt ON pm.PARTITION_ID = tbt.PARTITION_ID
WHERE tbt.STATE NOT IN ('NORMAL', 'RUNNING')
ORDER BY pm.DB_NAME, pm.TABLE_NAME, tbt.TABLET_ID;
```

---