---
type: case
category: node
issue: fe-oom-iceberg-query
keywords: [fe, oom, memory, iceberg, external-catalog, gc, delete-file, heap]
---

# Case-024: FE OOM Due to Iceberg Catalog External Table Query

## Environment

- StarRocks version: All versions
- Architecture: shared-nothing / shared-data
- External catalog: Iceberg

## Symptom

**Background:** User executes Iceberg table query jobs.

**Symptoms observed:**
- FE leader memory rises significantly after SQL submission
- CPU continuously increases
- Other query/import tasks report errors
- Process status shows frequent GC triggering service abnormality

**Process status example:**

```
PID USER      PR  NI    VIRT    RES    SHR S %CPU %MEM     TIME+ COMMAND
25486 app       20   0   30.1g  24.7g   4500 R 99.9 80.8  17:58.92 GC Thread#0
26471 app       20   0   30.1g  24.7g   4500 R 99.9 80.8  17:58.44 GC Thread#1
26472 app       20   0   30.1g  24.7g   4500 R 99.9 80.8  17:59.18 GC Thread#2
...
```

## Investigation

### Step 1: Check Process Status

Observe high memory usage and frequent GC threads:

```
Memory: 24.7g (80.8% of machine memory)
GC Threads: Multiple threads at 99.9% CPU
```

### Step 2: Check Iceberg Table Status

Iceberg table has many delete files that need maintenance.

## Root Cause

**Iceberg delete file accumulation:**

1. **Iceberg table maintenance**: Delete files accumulate over time
2. **Metadata overhead**: Querying Iceberg tables with many delete files requires significant metadata processing
3. **Memory pressure**: Large metadata processing causes FE memory spike
4. **GC pressure**: High memory usage triggers frequent GC, affecting service

## Resolution

### Immediate Recovery

1. Restart FE leader node
2. Service returns to normal

### Long-term Solution

**Option 1: Regularly maintain Iceberg tables**

```sql
-- Expire old snapshots to clean up delete files
-- Delete snapshots older than specified timestamp, retain last N snapshots
ALTER TABLE iceberg_catalog.db.table_name
EXECUTE expire_snapshots(older_than = '2025-01-01 00:00:00', retain_last = 2);

-- Rewrite data files (compact) to reduce file count and optimize layout
ALTER TABLE iceberg_catalog.db.table_name
EXECUTE rewrite_data_files("min_file_size_bytes" = 134217728);
```

**Option 2: Monitor Iceberg table health**

- Regularly check delete file count
- Schedule maintenance operations
- Monitor FE memory during Iceberg queries

## Key Commands

```bash
# Check process status
top -p <FE_pid>

# Check GC status
jstat -gcutil <FE_pid> 1000 10

# Check memory usage
curl http://fe_host:8030/metrics | grep -i memory
```

```sql
-- Check Iceberg table snapshots
SELECT * FROM iceberg_catalog.db.table_name.snapshots;

-- Expire old snapshots
-- With parameter key specified:
ALTER TABLE iceberg_catalog.db.table_name
EXECUTE expire_snapshots(older_than = '2025-01-01 00:00:00', retain_last = 2);

-- Without parameter key (position-based):
ALTER TABLE iceberg_catalog.db.table_name
EXECUTE expire_snapshots('2025-01-01 00:00:00', 2);

-- Rewrite data files (compact) with custom min file size
ALTER TABLE iceberg_catalog.db.table_name
EXECUTE rewrite_data_files("min_file_size_bytes" = 134217728);

-- Rewrite data files for specific partition
ALTER TABLE iceberg_catalog.db.table_name
EXECUTE rewrite_data_files("min_file_size_bytes" = 134217728) WHERE part_col = 'p1';
```

## Lessons Learned

1. **Iceberg maintenance**: Regularly clean up delete files to reduce metadata overhead
2. **External table queries**: Iceberg queries can cause significant FE memory pressure
3. **GC impact**: Frequent GC affects overall service stability
4. **Proactive maintenance**: Schedule regular Iceberg table maintenance

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [05-data-lake.md](../../skills/05-data-lake.md) — Data lake troubleshooting