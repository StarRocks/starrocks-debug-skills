---
type: case
category: node
issue: fe-oom-complex-sql-plan
keywords: [fe, oom, memory, plan, complex-sql, heap, concurrent-query, jvm]
---

# Case-022: FE OOM Due to Complex SQL Plan Memory Spike

## Environment

- StarRocks version: All versions
- Architecture: shared-nothing / shared-data

## Symptom

**Symptoms observed:**
- Heap memory suddenly increases
- Scheduled import tasks have widespread delays
- QPS and connection count remain in normal range

## Investigation

### Step 1: Check Memory Trends

Observe heap memory spike:

```
Heap memory: Sudden spike
Task scheduling: Delayed
```

### Step 2: Check Audit Logs and FE Logs

Audit logs and FE logs reveal:
- Many highly complex queries exist in concurrent scheduling
- Query plan parsing consumes large amounts of memory

```
# Audit log shows complex queries
SELECT ... FROM ... JOIN ... JOIN ... (complex nested queries)
```

### Step 3: Verify QPS and Connections

QPS and connection count are within normal range, indicating memory issue is caused by query complexity, not volume.

## Root Cause

**Complex SQL plan memory consumption:**

1. **Complex query plans**: Highly complex queries require significant memory for plan parsing
2. **Concurrent execution**: Multiple complex queries running simultaneously
3. **Memory spike**: Combined effect causes sudden heap memory increase
4. **Task delays**: Memory pressure causes import task scheduling delays

## Resolution

### Immediate Recovery

**Scale FE service memory:**

1. Stop FE service
2. Increase machine memory
3. Increase JVM `-Xmx` configuration
4. Restart FE service

```
# Example: Increase heap to 32GB
-Xmx32768m
```

### Long-term Solution

**Option 1: Split complex SQL**

```sql
-- Instead of one complex query, split into multiple simpler queries
-- Original: Complex query with multiple joins and nested subqueries
-- Split: Multiple simpler queries with intermediate results
```

**Option 2: Monitor memory during peak periods**

- Observe memory usage during peak operation periods
- Keep memory usage below 80% threshold
- Add more FE nodes if needed

## Key Commands

```bash
# Check FE JVM heap
jstat -gc <FE_pid> 1000 10

# Check memory usage
curl http://fe_host:8030/metrics | grep -i memory

# Export heap histogram (no Full GC)
jmap -histo <FE_pid> > jmap.txt
```

```sql
-- Check running queries
SHOW RUNNING QUERIES;

-- Alternative: check current queries on FE node
SHOW PROC '/current_queries';

-- Alternative: check all queries including pending ones
SHOW PROCESSLIST;

-- Kill specific query if needed
KILL QUERY '<query_id>';
```

## Lessons Learned

1. **Query complexity vs volume**: Memory issues can come from query complexity, not just volume
2. **Split complex queries**: Prefer simpler queries over complex ones
3. **Memory threshold**: Keep peak memory usage below 80%
4. **Scale FE memory**: Production environments should have adequate FE memory allocation

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [case-021-fe-oom-metadata-operations.md](case-021-fe-oom-metadata-operations.md) — Metadata operations memory issue