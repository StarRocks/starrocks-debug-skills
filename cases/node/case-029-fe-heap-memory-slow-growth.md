---
type: case
category: node
issue: fe-heap-memory-slow-growth
keywords: [fe, memory, leak, heap, replica, drop, swap, insert-overwrite, upgrade, tablet-delete]
---

# Case-029: FE Heap Memory Slow Growth Due to Tablet Delete Optimization Bug

## Environment

- StarRocks version:
  - Affected: 3.3.13 - 3.3.17
  - Fixed: 3.3.18+, 3.4.7+, 3.5.4+
- Architecture: shared-nothing / shared-data

## Symptom

**Version-specific issue:** Problem occurs between versions 3.3.13 - 3.3.18.

If FE heap memory continuously slowly grows, this issue may be the cause.

**Symptoms observed:**
- FE heap memory slowly and continuously grows
- jmap shows `com.starrocks.catalog.Replica` occupying excessive memory

## Investigation

### Step 1: Check Memory Trend

Observe FE heap memory slowly increasing over time.

### Step 2: Export jmap for Analysis

```bash
# Export heap histogram (no Full GC trigger)
jmap -histo <FE_pid> > jmap.txt
```

### Step 3: Analyze jmap Output

In exported file, observe:

```
# Replica class occupying excessive memory
com.starrocks.catalog.Replica
```

**Key finding:**
- Replica objects occupy excessive memory
- Indicates memory leak in Replica management

### Step 4: Check Operation Patterns

Operations that may trigger this issue:
- Large amount of DROP operations
- SWAP operations
- INSERT OVERWRITE operations

### Step 5: Check Related PRs

**3.3.13 optimization PR:**
[BugFix] Fix recycle bin missing to delete lake mv's expired partitions after mv refreshed (backport)

**3.3.18 fix PR:**
https://github.com/StarRocks/starrocks/pull/61582

## Root Cause

**Tablet delete optimization memory leak:**

1. **3.3.13 optimization**: Tablet deletion path optimization was introduced
2. **Memory leak**: Optimization caused Replica object memory leak
3. **Triggering operations**: DROP, swap, INSERT OVERWRITE operations
4. **Version scope**: Bug only exists in 3.3.13 - 3.3.17

## Resolution

### Short-term Workaround

Restart FE.

### Long-term Solution

Upgrade version.

**Fixed versions:**
- 3.3.18+
- 3.4.7+
- 3.5.4+

## Key Commands

```bash
# Export heap histogram (no Full GC)
jmap -histo <FE_pid> > jmap.txt

# Check Replica memory usage in jmap output
grep "com.starrocks.catalog.Replica" jmap.txt

# Check memory usage
curl http://fe_host:8030/metrics | grep -i memory

# Check GC status
jstat -gcutil <FE_pid> 1000 10

# Check recent operations in audit log
grep -E "DROP|INSERT OVERWRITE" fe.audit.log | tail -100
```

```sql
-- Check recent table operations from information_schema
SELECT TABLE_NAME, CREATE_TIME FROM information_schema.tables 
ORDER BY CREATE_TIME DESC LIMIT 20;

-- Check tablet info for specific table
SHOW TABLET FROM table_name;
```

## Lessons Learned

1. **Version-specific bugs**: Track bug scope and fixed versions
2. **jmap analysis**: Use jmap to identify leaked object types
3. **Operation patterns**: Memory leaks often triggered by specific operations
4. **Proactive upgrade**: Upgrade to fixed versions promptly

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [case-007-memory-tracking-leak.md](case-007-memory-tracking-leak.md) — Memory tracking leak
- [case-025-fe-oom-insert-memory-leak.md](case-025-fe-oom-insert-memory-leak.md) — Insert memory leak