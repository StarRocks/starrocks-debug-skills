---
type: case
category: node
issue: fe-oom-insert-memory-leak
keywords: [fe, oom, memory, leak, insert, insertloadjob, jmap, heap, upgrade]
---

# Case-025: FE OOM Due to Insert Memory Leak

## Environment

- StarRocks version: 
  - Affected: 3.1.0 ~ 3.1.16, 3.2.0 ~ 3.2.12, 3.3.0 ~ 3.3.7
  - Fixed: 3.1.17+, 3.2.13+, 3.3.8+
- Architecture: shared-nothing / shared-data

## Symptom

**Background:** 
- User discovers FE memory abnormally rising during usage
- Process restarts without normal leader switch
- Main cluster task is import jobs

**Symptoms observed:**
- FE memory continuously increases
- FE process crashes/restarts
- Leader switch does not occur normally

## Investigation

### Step 1: Export jmap for Analysis

```bash
# Export heap histogram (no Full GC trigger)
jmap -histo <FE_pid> > jmap.txt
```

### Step 2: Analyze jmap Output

In exported file, observe:

```
14:        521358      154321968  com.starrocks.load.loadv2.InsertLoadJob
```

- `InsertLoadJob` instances: 520,000+
- Instance count over 10,000 indicates memory leak
- This is a confirmed memory leak

### Step 3: Check Related Issue

Issue: https://github.com/StarRocks/starrocks/issues/53810
PR: https://github.com/StarRocks/starrocks/pull/53809

## Root Cause

**InsertLoadJob memory leak bug:**

1. **Bug versions**: 3.1.0-3.1.16, 3.2.0-3.2.12, 3.3.0-3.3.7
2. **Insert operations**: Frequent insert operations cause InsertLoadJob instances to accumulate
3. **Memory leak**: InsertLoadJob objects not properly released
4. **Memory exhaustion**: Accumulated instances consume heap memory, causing OOM

## Resolution

### Immediate Recovery

Upgrade cluster version.

### Upgrade Target

**Fixed versions:**
- 3.1.17+
- 3.2.13+
- 3.3.8+

### Upgrade Procedure

```bash
# 1. Prepare new version package
# 2. Stop FE nodes one by one (maintain majority)
# 3. Replace FE package
# 4. Start FE nodes
# 5. Verify cluster health
```

## Key Commands

```bash
# Export heap histogram (no Full GC)
jmap -histo <FE_pid> > jmap.txt

# Check InsertLoadJob count in jmap output
grep InsertLoadJob jmap.txt

# Check memory usage
curl http://fe_host:8030/metrics | grep -i memory

# Check GC status
jstat -gcutil <FE_pid> 1000 10
```

```sql
-- Check running load jobs
SHOW LOAD;

-- Check load jobs from specific database
SHOW LOAD FROM database_name;
```

## Lessons Learned

1. **Memory leak detection**: Use `jmap -histo` to identify leaked objects
2. **Threshold check**: Object count over 10,000 usually indicates memory leak
3. **Version tracking**: Track known memory leak bugs and fixed versions
4. **Proactive upgrade**: Upgrade to fixed versions promptly

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [case-007-memory-tracking-leak.md](case-007-memory-tracking-leak.md) — Memory tracking leak