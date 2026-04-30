---
type: case
category: node
issue: fe-machine-memory-growth-malloc-arena
keywords: [fe, memory, machine, heap-off-memory, glibc, malloc_arena_max, thread-arena, direct-memory]
---

# Case-030: FE Machine Memory Growth Exceeding 95% Due to glibc Thread Arena

## Environment

- StarRocks version: 3.3 and below (leader FE node more affected)
- Architecture: shared-nothing / shared-data
- System: Linux with glibc 2.10+

## Symptom

**Normal behavior:** FE process typically occupies ~130% of JVM allocated heap memory, and does not continue requesting more machine memory.

**Symptoms observed:**
- Machine memory continues to grow
- Memory usage exceeds 95%+
- Heap memory appears normal, but off-heap memory grows continuously
- Issue manifests more prominently on leader FE nodes in versions 3.3 and below

## Investigation

### Step 1: Check Machine Memory

```
Machine memory: >95% usage
FE heap: Normal
Off-heap memory: Growing
```

### Step 2: Understand glibc Thread Arena

**Background:**
- Since glibc 2.10, thread arenas were introduced
- When a thread requests memory, glibc creates a thread arena (typically 64MB)
- Thread arenas are not exclusive to single threads
- All thread arenas are added to a circular linked list and shared among all threads

Reference: https://www.easyice.cn/archives/341

**Memory calculation:**
Maximum thread arena memory = 64MB × CPU cores × 8

This causes:
- Heap memory normal
- Off-heap memory continuously growing
- Problem more prominent on leader FE nodes in 3.3 and below

## Root Cause

**glibc thread arena memory allocation:**

1. **Thread arena**: glibc creates 64MB thread arenas for memory requests
2. **Arena accumulation**: Thread arenas not released, accumulate up to (64MB × cores × 8)
3. **Off-heap growth**: Causes off-heap memory to grow beyond heap allocation
4. **Leader impact**: Leader FE has more threads, more affected

## Resolution

### Configure MALLOC_ARENA_MAX

Set `MALLOC_ARENA_MAX=1` to disable thread arenas.

**fe.conf configuration:**

```
# Add to fe.conf (uppercase, same as JAVA_OPTS)
MALLOC_ARENA_MAX=1
```

**Procedure:**

1. Add `MALLOC_ARENA_MAX=1` to fe.conf
2. Rolling restart FE nodes
3. Verify memory usage stabilizes

**Note:**
- This significantly reduces off-heap memory usage
- May impact performance, observe P99 latency
- If P99 latency increases, try switching leader node to observe

## Key Commands

```bash
# Check machine memory
free -m

# Check FE process memory
ps aux | grep fe

# Check glibc version
ldd --version

# Check CPU cores
nproc
```

```sql
-- Check all FE nodes status (identify leader from IsLeader column)
SHOW FRONTENDS;
```

## Lessons Learned

1. **glibc behavior**: Thread arenas can cause significant off-heap memory usage
2. **Configuration control**: MALLOC_ARENA_MAX=1 limits off-heap growth
3. **Performance trade-off**: May impact P99 latency, monitor after change
4. **Version scope**: Issue more prominent in 3.3 and below, leader nodes

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [08-deployment.md](../../skills/08-deployment.md) — Deployment and configuration