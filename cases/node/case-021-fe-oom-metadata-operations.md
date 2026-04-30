---
type: case
category: node
issue: fe-oom-metadata-operations
keywords: [fe, oom, memory, metadata, create-table, drop-table, heap, force]
---

# Case-021: FE OOM Due to Frequent Metadata Operations

## Environment

- StarRocks version: All versions
- Architecture: shared-nothing / shared-data

## Symptom

Customer's business requires periodic table creation and cleanup:
- Sequential operations creating dozens of tables per second
- Synchronous deletion operations
- Creating hundreds of thousands of tables in total process

**Symptoms observed:**
- Heap memory and CPU continuously increase
- Query response becomes slow
- FE log shows massive table creation and deletion operations

## Investigation

### Step 1: Check FE Metrics

Observe heap memory and CPU trends:

```
Heap memory: Rising trend
CPU usage: Rising trend
Query latency: Increasing
```

### Step 2: Check FE Logs

FE log shows large number of table creation and deletion operations:

```
CREATE TABLE ...
DROP TABLE ...
CREATE TABLE ...
DROP TABLE ...
... (massive operations)
```

## Root Cause

**Metadata pressure from frequent operations:**

1. **Drop table without force**: When dropping tables without `FORCE`, metadata still occupies memory (pending cleanup)
2. **High concurrency**: Creating dozens of tables per second puts pressure on metadata management
3. **Metadata accumulation**: Hundreds of thousands of operations cause metadata memory to accumulate

## Resolution

### Immediate Recovery

1. Stop table creation/deletion jobs
2. Restart FE service
3. Service returns to normal

### Long-term Solution

**Option 1: Use FORCE when dropping tables**

```sql
-- Drop table with FORCE to immediately release metadata
DROP TABLE IF EXISTS table_name FORCE;
```

**Option 2: Reduce table creation concurrency**

- Lower frequency of table creation operations
- Batch operations instead of continuous high-frequency operations
- Monitor memory usage during peak periods

## Key Commands

```bash
# Check FE memory usage
curl http://fe_host:8030/metrics | grep -i memory

# Check FE JVM heap
jstat -gc <FE_pid> 1000 10

# Check FE logs for table operations
grep -E "CREATE TABLE|DROP TABLE" fe.log | tail -100
```

```sql
-- Drop table with FORCE
DROP TABLE IF EXISTS table_name FORCE;

-- Monitor running queries
SHOW RUNNING QUERIES;

-- Alternative: check current queries on FE node
SHOW PROC '/current_queries';

-- Alternative: check all pending queries in queue
SHOW PROCESSLIST;
```

## Lessons Learned

1. **FORCE drop**: Use `DROP TABLE ... FORCE` to immediately release metadata memory
2. **Operation frequency**: High-frequency metadata operations put significant pressure on FE
3. **Batch operations**: Prefer batch operations over continuous high-frequency operations
4. **Memory monitoring**: Regularly monitor FE memory during intensive metadata operations

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [case-008-be-oom.md](case-008-be-oom.md) — BE OOM troubleshooting