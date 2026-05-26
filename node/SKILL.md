---
name: 03-node
description: "Use when a BE or FE node is abnormal: BE out-of-memory or crash, FE deadlock causing all queries to hang, FE Full GC pauses, or FE heap memory growth and leak. Covers mem_tracker curl analysis, large-memory-alloc log search (query_id → SQL), jstack + DeadLockChecker deadlock tracing, GC log analysis, and memory allocation flame graph interpretation."
version: 2.0.0
category: node
keywords:
- BE crash
- BE OOM
- FE deadlock
- FE Full GC
- FE heap memory
- memory tracker
- lock ordering
- DeadLockChecker
- jstack
- fair lock
tools:
- top
- curl
- jstack
- find
- grep
- python3
- tail
- cat
- analyze_logs.py
related_cases:
- case-003-fe-deadlock
- case-007-memory-tracking-leak
- case-008-be-oom
- case-021-fe-oom-metadata-operations
- case-022-fe-oom-complex-sql-plan
- case-023-fe-oom-mv-frequent-refresh
- case-024-fe-oom-iceberg-query
- case-025-fe-oom-insert-memory-leak
- case-026-fe-crash-async-profiler
- case-027-fe-oom-heap-config-insufficient
- case-029-fe-heap-memory-slow-growth
- case-030-fe-machine-memory-growth-malloc-arena
- case-031-fe-machine-memory-spike-traffic
---

# 03 - Node Troubleshooting (BE Crash / OOM, FE Deadlock / GC)

Investigation guide for BE-level crashes and out-of-memory events, FE deadlocks,
Full GC pauses, and FE heap memory analysis.

Five root causes account for the vast majority of cases:

- **Cause A** — Query-caused BE OOM (memory limit exceeded by large query)
- **Cause B** — BE crash (segfault / internal fatal error)
- **Cause C** — FE deadlock caused by inconsistent lock ordering
- **Cause D** — FE Full GC caused by heap too small or memory leak
- **Cause E** — FE OOM from specific module memory leak

---

## Metric Taxonomy — Read This First

Before using any metrics, understand the two-layer structure:

### BE-Side Metrics

| Metric / Source | Meaning |
|---|---|
| `curl http://<BE>:<port>/mem_tracker` | Per-module memory breakdown (current allocations by subsystem) |
| `curl http://<BE>:<port>/memz` | tcmalloc allocator internal status — heap vs OS footprint |
| `starrocks_be_.*_mem_bytes` | Per-module memory gauge in Prometheus/Grafana (e.g., `starrocks_be_query_mem_bytes`) |
| `be.WARNING` — `large memory alloc` | Single large allocation events, includes `query_id` and stack trace |
| `dmesg` — OOM Killer | Linux kernel OOM Killer records listing killed process and memory pressure |

### FE-Side Metrics

| Metric / Source | Meaning |
|---|---|
| `fe.gc.log` — Full GC entries | JVM Full GC frequency, stop-the-world pause duration, Old Gen occupancy |
| `fe.WARNING` — `FE ShutDown! Running Query` | Queries running at moment of FE OOM shutdown, each with `QueryFEAllocatedMemory` |
| `fe.log` — `LockManager` / `DeadlockChecker` | Structured JSON deadlock report: lock chain, waiter threads, hold durations |
| Memory Allocated Profile (flame graph) | HTML flame graph in `fe/log/proc_profile/` — per-callsite allocation breakdown |
| `SHOW PROC '/current_queries'` | Live view of running queries with `memUsageBytes` (FE) and `memUsageBytes` (BE) |

### How to Retrieve Metrics

**BE memory breakdown**

```bash
# Per-module memory (most useful for OOM investigation)
curl -s http://<BE_IP>:<BE_HTTP_PORT>/mem_tracker

# All BE memory gauge metrics
curl -s http://<BE_IP>:<BE_HTTP_PORT>/metrics | grep "^starrocks_be_.*_mem_bytes"

# tcmalloc allocator status
curl -s http://<BE_IP>:<BE_HTTP_PORT>/memz

# Memory-heavy queries from mem_tracker
curl -s http://<BE_IP>:<BE_HTTP_PORT>/mem_tracker | grep "query"
```

**BE log signals**

```bash
# Large allocation events (include query_id)
grep "large memory alloc" be.WARNING

# OOM Killer evidence
dmesg | grep -i "oom\|killed process\|out of memory" | tail -30

# Last entries before crash
tail -200 be.WARNING
tail -200 be.ERROR
```

**FE GC and OOM signals**

```bash
# Full GC frequency and pause duration
grep "Full GC\|GC pause" fe.gc.log | tail -50

# Old Gen occupancy trend
grep "Old" fe.gc.log | tail -30

# Queries running at FE OOM shutdown
grep "FE ShutDown! Running Query" fe.WARNING

# Memory audit post-incident (top N by FE memory)
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "QueryFEAllocatedMemory" 3 fe.audit.log

# Memory audit post-incident (top N by BE memory)
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "MemCostBytes" 3 fe.audit.log
```

**FE deadlock signals**

```bash
# Version 3.3+
grep "LockManager" fe.log | tail -50

# Version 3.2 and earlier
grep "DeadlockChecker" fe.log | tail -50

# Capture thread dump for manual analysis
jstack -l <fe_pid> > /tmp/fe_jstack.log

# Find blocked threads
grep "parking to wait for\|BLOCKED" /tmp/fe_jstack.log | head -30
```

**Key rules for interpretation**:

1. When `dmesg` shows `Killed process ... (starrocks_be)` — the OS killed the BE process, not a StarRocks internal error. Root cause is physical memory exhaustion, not a code bug.
2. `large memory alloc` entries in `be.WARNING` identify the query_id responsible for the spike. One or two queries can exhaust all BE memory without hitting any per-query limit if `query_mem_limit` is 0 or very high.
3. FE Full GC pauses > 10 s will cause BE heartbeat timeouts — the FE appears transiently dead. Check `fe.gc.log` before concluding FE is crashed.
4. `LockManager` / `DeadlockChecker` JSON in `fe.log` is the fastest path to deadlock confirmation — it contains the complete lock chain with thread names and IDs.
5. `QueryFEAllocatedMemory` in `fe.WARNING` at shutdown is the most direct signal for identifying which query caused FE OOM — match the query_id to `fe.audit.log` for the SQL text.

---

## Node Issue Type Reference

| Symptom | Issue Type | First Check |
|---|---|---|
| BE process missing from `SHOW BACKENDS`; `dmesg` shows `Killed process` | BE OOM (Linux OOM Killer) | `dmesg`; `be.WARNING` for `large memory alloc` |
| BE process missing; no OOM in `dmesg`; core dump present | BE crash (segfault) | Core dump stack trace; `be.WARNING` last 200 lines |
| Queries return `version does not exist`; DDL hangs | FE deadlock | `fe.log` for `LockManager` JSON; `jstack` |
| FE queries slow; `SHOW FRONTENDS` shows transient `Alive=false` | FE Full GC | `fe.gc.log` Full GC frequency and pause duration |
| FE process exits; `fe.WARNING` shows `FE ShutDown! Running Query` | FE OOM (heap exhausted) | `fe.WARNING` for `QueryFEAllocatedMemory`; flame graph |
| BE RSS grows without bound over days; no single large allocation | BE memory leak | `mem_tracker` trend; upgrade to patched version |

---

## Phase 1 — Classify Node Issue

**Goal**: determine issue type in under 30 seconds before deeper investigation.

### Step 1.1 — BE or FE?

```bash
# Check whether BE process is alive
SHOW BACKENDS;  -- Alive column; LastHeartbeat timestamp

# Check whether FE is alive
SHOW FRONTENDS;  -- Alive column

# If BE is down — check OOM Killer
dmesg | grep -i "oom\|killed process\|out of memory" | tail -20
```

| Observation | Next Step |
|---|---|
| BE missing from `SHOW BACKENDS`; `dmesg` has OOM entry | Step 1.2 — BE OOM path |
| BE missing; no OOM in `dmesg` | Step 1.2 — BE crash path |
| FE queries hang or `version does not exist` errors | Step 1.3 — FE issue path |
| FE transiently `Alive=false`; recovers after seconds | Step 1.3 — FE Full GC path |

### Step 1.2 — BE: OOM or Crash?

```bash
# OOM: Linux kernel killed the process
dmesg | grep -i "killed process" | grep "starrocks_be"
# → If present: Cause A (BE OOM)

# Crash: core dump or signal in log
ls -lh /proc/sys/kernel/core_pattern   # find core dump location
tail -50 be.WARNING
# → Look for "Segmentation fault", "SIGABRT", "Fatal signal"
# → If present: Cause B (BE crash)
```

### Step 1.3 — FE: Deadlock or GC / OOM?

```bash
# Deadlock: look for LockManager JSON in fe.log
grep "LockManager\|DeadlockChecker" fe.log | tail -5
# → If present: Cause C (FE deadlock)

# Full GC: check GC log for recent Full GC
grep "Full GC" fe.gc.log | tail -5
# → If recent Full GC with pause > 5s: Cause D (FE Full GC)

# OOM shutdown: look for shutdown message
grep "FE ShutDown! Running Query" fe.WARNING | tail -5
# → If present: Cause E (FE OOM)
```

---

## Phase 2 — Locate Root Cause

### Step 2.1 — BE OOM: Which Query or Workload?

```bash
# Step 1: Find time window of memory spike via monitoring (Grafana or RSS trend)
# Step 2: Find large allocation events in that window
grep "large memory alloc" be.WARNING
# Output includes query_id and allocation size in bytes

# Step 3: Match query_id to SQL
grep "<query_id>" fe.audit.log
# Or check live queries if incident is ongoing:
```

```sql
SHOW PROC '/current_queries';
-- Column: memUsageBytes — sort descending to find the largest consumer
```

```bash
# Step 4: Check overall module breakdown
curl -s http://<BE_IP>:<BE_HTTP_PORT>/mem_tracker
# Look for which module (query, load, compaction, metadata) dominates
```

**Pattern match**:

| Pattern | Points toward |
|---|---|
| One or two `large memory alloc` entries > 1 GB from HashJoin / Sort | Single large query; → Cause A |
| `mem_tracker` shows load module dominates | Ingestion-side OOM; check `mem_limit` vs load concurrency |
| RSS grows slowly over days; `mem_tracker` shows small allocations each time | Memory tracking leak; upgrade to patched version |

→ **Go to Phase 3, Cause A**

### Step 2.2 — BE Crash: Core Dump Analysis

```bash
# Confirm core dump location
cat /proc/sys/kernel/core_pattern

# Check file descriptor limit
cat /proc/<be_pid>/limits | grep "open files"

# Review log immediately before crash
tail -200 be.WARNING
tail -200 be.ERROR

# If core dump present, get stack trace
gdb starrocks_be <core_file> -batch -ex "thread apply all bt" 2>/dev/null | head -200
```

→ **Go to Phase 3, Cause B**

### Step 2.3 — FE Deadlock: jstack + DeadLockChecker

**Method 1 — DeadLockChecker (fastest, v2.5.15+ / 3.0.9+ / 3.1.6+ / 3.2.0+)**

```bash
# Version 3.3+
grep "LockManager" fe.log | tail -20

# Version 3.2 and earlier
grep "DeadlockChecker" fe.log | tail -20
```

The output is structured JSON:
- `lockDbName` — database held
- `lockState` — `readLocked` or `writeLocked`
- `slowReadLockCount` — long-held read locks (threshold: `slow_lock_threshold_ms`, default 3000 ms)
- `lockHoldTime` — write lock hold duration (ms)
- `threadInfo` — stack trace of lock holder
- `lockWaiters` — threads waiting for this lock

To confirm circular wait: match `threadInfo` thread IDs against other locks' `lockWaiters`.

**Method 2 — jstack (when LockManager JSON is absent)**

```bash
# Capture thread dump
jstack -l <fe_pid> > /tmp/fe_jstack.log

# Find threads waiting for locks
grep "parking to wait for" /tmp/fe_jstack.log

# Find the lock holder for a given lock address
grep -A 5 "Locked ownable synchronizers" /tmp/fe_jstack.log | grep "<lock_address>"

# Identify long-running threads: convert PID from top -Hp to hex
top -Hp <fe_pid> -b -n 1 > top_output.txt
printf "%x\n" <thread_pid>   # e.g., 19700 → 4cf4
grep "nid=0x4cf4" /tmp/fe_jstack.log
```

→ **Go to Phase 3, Cause C**

### Step 2.4 — FE Heap: GC Log + Flame Graph

```bash
# Check GC frequency and pause duration
grep "Full GC" fe.gc.log | tail -30

# Check Old Gen occupancy trend
grep "Old" fe.gc.log | tail -30

# Check if FE shut down due to OOM
grep "FE ShutDown! Running Query" fe.WARNING

# Locate flame graph profiles (v3.3.6+)
ls -lh fe/log/proc_profile/
# Files are .tgz archives containing HTML flame graphs

# Audit which SQL consumed most FE memory post-incident
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "QueryFEAllocatedMemory" 3 fe.audit.log
```

→ **Go to Phase 3, Cause D or Cause E**

---

## Phase 3 — Take Action by Cause

### Cause A — Query-Caused BE OOM

**Confirm all match**:
- `dmesg` shows OOM Killer killed `starrocks_be`
- `be.WARNING` has `large memory alloc` entries > 500 MB pointing to HashJoin, Sort, or Agg operators
- No or very high `query_mem_limit` configured

**Mechanism**: A single large query allocates memory for HashJoin build side or Sort buffer. Without a per-query limit, it can consume all available BE memory. When RSS exceeds the system memory threshold, the Linux OOM Killer sends SIGKILL. The BE process is forcibly terminated — not a graceful shutdown. In-flight transactions cannot publish, causing downstream publish stalls and replica recovery.

**Immediate: Kill the offending query**

```sql
-- Find the query_id from be.WARNING large memory alloc entry
KILL QUERY '<query_id>';
```

**Set per-query memory limit**

```sql
-- Session level
SET query_mem_limit = 17179869184;  -- 16 GB example

-- Global level
SET GLOBAL query_mem_limit = 17179869184;
```

**Enable query spill to disk**

```sql
SET enable_query_spill = true;
SET spill_mem_table_size = 268435456;  -- 256 MB spill trigger
```

**Protect BE memory**

```bash
# Set mem_limit in be.conf to leave OS headroom (e.g., 80% of physical RAM)
mem_limit = 80%

# Restart BE to apply
```

**Monitor recovery**

```bash
# Confirm BE rejoins cluster
# SHOW BACKENDS — wait for Alive=true

# Check publish queue drains
curl -s http://<BE_IP>:<BE_HTTP_PORT>/metrics | grep "publish_version_queue"
```

---

### Cause B — BE Crash (Segfault)

**Confirm all match**:
- BE process missing; `dmesg` has no OOM Killer entry
- `be.WARNING` or `be.ERROR` shows `Fatal signal`, `Segmentation fault`, or `SIGABRT`
- Core dump file present at configured location

**Mechanism**: A software bug (null pointer dereference, use-after-free, assertion failure) causes the BE process to receive a fatal signal. The OS terminates the process immediately. Unlike OOM, no memory pressure is involved — this is a code defect requiring engineering investigation.

**Immediate: Collect evidence before restart**

```bash
# Copy core dump and logs before restarting (they may be overwritten)
cp <core_file> /tmp/be_crash_$(date +%Y%m%d_%H%M%S).core
cp be.WARNING /tmp/be_WARNING_$(date +%Y%m%d_%H%M%S).txt
cp be.ERROR /tmp/be_ERROR_$(date +%Y%m%d_%H%M%S).txt

# Get stack trace from core dump (requires gdb and matching binary)
gdb starrocks_be <core_file> -batch \
  -ex "thread apply all bt" \
  -ex "info registers" 2>/dev/null | head -300 > /tmp/crash_stack.txt
```

**Check fd limit**

```bash
# Low fd limit causes silent failures that can trigger crashes
cat /proc/<be_pid>/limits | grep "open files"
# Increase if < 65536:
ulimit -n 65536
# Or set in /etc/security/limits.conf permanently
```

**Restart BE and monitor**

```bash
# Restart
./bin/stop_be.sh && sleep 5 && ./bin/start_be.sh

# Confirm rejoins
SHOW BACKENDS;
```

**Escalate**: provide crash stack, BE version (`./bin/starrocks_be --version`), and reproduction steps to engineering.

---

### Cause C — FE Deadlock (Inconsistent Lock Ordering)

**Confirm all match**:
- `fe.log` contains `LockManager` or `DeadlockChecker` JSON with circular wait
- jstack shows two or more threads mutually blocked on different database lock addresses
- Queries return `version does not exist`; DDL operations hang

**Mechanism**: StarRocks uses fair `ReentrantReadWriteLock` (FIFO order) for database locks. In FIFO mode, a queued write lock blocks all subsequent read lock requests — even if another read lock is currently held. When Thread A holds Lock-1 and waits for Lock-2, while Thread B holds Lock-2 and waits for Lock-1, neither can proceed. The tablet report processing thread may be one of the blocked threads, causing version updates to freeze and the FE to recycle versions still needed by active queries.

**Immediate: Capture evidence and restart FE**

```bash
# Capture multiple jstack snapshots 30s apart
for i in 1 2 3; do
  jstack -l <fe_pid> > /tmp/fe_jstack_${i}.log
  sleep 30
done

# Restart FE to restore service
./bin/stop_fe.sh && sleep 5 && ./bin/start_fe.sh
```

**Identify the deadlock pattern**

```bash
grep "LockManager" fe.log | python3 -c "
import sys, json, re
for line in sys.stdin:
    m = re.search(r'\[(\[.*\])\]', line)
    if m:
        data = json.loads('[' + m.group(1) + ']')
        for lock in data:
            print('DB:', lock.get('lockDbName'), '| State:', lock.get('lockState'))
            print('  Holder thread:', lock.get('threadInfo','')[:100])
            for w in lock.get('lockWaiters', []):
                print('  Waiter:', w.get('threadName'), 'id:', w.get('threadId'))
"
```

**Check version against historical fixes**

| PR | Issue | Fix |
|---|---|---|
| [#29432](https://github.com/StarRocks/starrocks/pull/29432) | Multi-database query lock ordering | Sort databases alphabetically before locking |
| [#29360](https://github.com/StarRocks/starrocks/pull/29360) | Database lock ordering inconsistency | Standardize lock acquisition order |
| [#30703](https://github.com/StarRocks/starrocks/pull/30703) | TabletInvertedIndex vs Database lock | Remove db lock check in tabletReport |
| [#32803](https://github.com/StarRocks/starrocks/pull/32803) | MV refresh lock ordering | Move SQL planning outside database lock |
| [#34237](https://github.com/StarRocks/starrocks/pull/34237) | TabletScheduler lock upgrade | Remove db lock when deleting CLONE replica |
| [#35736](https://github.com/StarRocks/starrocks/pull/35736) | MV nested lock issue | Plan execution moved outside database lock |
| [#15619](https://github.com/StarRocks/starrocks/pull/15619) | Long plan execution | Remove database lock requirement for planning |
| [#34272](https://github.com/StarRocks/starrocks/pull/34272) | External system access in lock | JDBC jar download outside lock |

**Common deadlock types**

| Type | Description |
|---|---|
| Inconsistent lock ordering | Thread A: lock(db1) → lock(db2); Thread B: lock(db2) → lock(db1) |
| Cross-lock ordering | TabletInvertedIndex vs Database acquired inconsistently |
| Lock upgrade | Thread holds read lock, attempts write lock |
| Thread pool exhaustion | Pool full of tasks waiting for nested async operations |
| Long lock hold | Lock held during slow planning or external system access |

---

### Cause D — FE Full GC (Heap Too Small or Leak)

**Confirm all match**:
- `fe.gc.log` shows Full GC with pause > 5 s occurring frequently (e.g., every few minutes)
- Old Gen occupancy does not decrease after GC (indicates leak) or is consistently near 100%
- FE is not OOM-killed but queries time out during GC pauses

**Mechanism**: JVM Full GC is a stop-the-world event — all application threads pause until GC completes. For large heaps (> 32 GB) with G1GC, a single Full GC can pause 10–60 s. During this pause, BE heartbeats are not sent, causing BEs to report the FE as dead. In-flight query plans are not dispatched, causing client-side timeouts. If the heap is too small for the metadata volume or a leak causes progressive Old Gen growth, Full GCs become frequent and the cluster is effectively unusable.

**Increase FE heap (requires restart)**

```bash
# Edit fe.conf
JAVA_OPTS="-Xmx32g -Xms32g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -XX:G1HeapRegionSize=32m"

# Restart FE
./bin/stop_fe.sh && sleep 5 && ./bin/start_fe.sh
```

**Enable memory profiling for leak diagnosis**

```bash
# v3.3.6+: profiles auto-generated in fe/log/proc_profile/
ls fe/log/proc_profile/

# v3.3.5 and below — add to fe.conf:
proc_profile_cpu_enable = false
proc_profile_collect_interval_s = 300

# Or run profiler manually:
mkdir -p mem_alloc_log
./bin/profiler.sh -e alloc --alloc 2m -d 300 -f mem_alloc_log/alloc-$(date +%s).html $(cat bin/fe.pid)
```

**Enable Memory Usage Tracker (v3.3.7+)**

```bash
# Logs per-module memory consumption automatically
# Search fe.log for MemoryUsageTracker output
grep "MemoryUsageTracker" fe.log | tail -20
```

---

### Cause E — FE OOM (Specific Module Leak)

**Confirm all match**:
- `fe.WARNING` contains `FE ShutDown! Running Query` with high `QueryFEAllocatedMemory`
- Flame graph shows one module (metadata, task scheduler, catalog) dominating allocations
- Issue recurs after FE restart and the same workload is applied

**Mechanism**: The JVM heap is exhausted by unbounded memory growth in a specific module — typically metadata cache without eviction limits, a task scheduler retaining completed tasks, or a query planner generating very large plan objects. Unlike Full GC (which eventually cleans up), a true leak means Old Gen never decreases; GC runs continuously but cannot reclaim memory; eventually `OutOfMemoryError` is thrown and the FE process exits.

**Immediate: Identify the leaking query**

```bash
grep "FE ShutDown! Running Query" fe.WARNING
# Output shows each running query's QueryFEAllocatedMemory
# Identify the largest consumer → match query_id to fe.audit.log for SQL text
```

**Quick fixes**

```bash
# Increase heap as temporary mitigation
JAVA_OPTS="-Xmx48g -Xms48g -XX:+UseG1GC"

# Add full stack traces for debugging (add to JAVA_OPTS)
-XX:-OmitStackTraceInFastThrow

# Add to fe.conf for verbose GC logging
-XX:+PrintGCDetails -XX:+PrintGCDateStamps -Xloggc:fe/log/fe.gc.log
```

**Capture allocation profile for engineering**

```bash
# Run profiler during high-memory period
./bin/profiler.sh -e alloc --alloc 512k -d 600 \
  -f /tmp/fe_alloc_$(date +%s).html $(cat bin/fe.pid)
```

**Known FE OOM patterns**

| Scenario | Module | Fix |
|---|---|---|
| Frequent metadata operations | Metadata cache | Reduce refresh frequency; upgrade |
| Complex SQL plan spike | Planner | Increase heap; reduce query complexity |
| Frequent MV refresh | MV scheduler | Increase MV refresh interval |
| Iceberg catalog query | Catalog cache | Add cache size limits |
| INSERT memory leak | INSERT executor | Upgrade to fixed version |

---

## Phase 4 — Verify Recovery

**BE OOM / Crash recovery**

```bash
# BE must be Alive=true and LastHeartbeat recent
SHOW BACKENDS;

# Publish queue must drain to zero
curl -s http://<BE_IP>:<BE_HTTP_PORT>/metrics | grep "publish_version_queue"

# No new load failures
SELECT LABEL, STATE, ERROR_MSG
FROM information_schema.loads
WHERE STATE = 'FAILED'
ORDER BY LOAD_FINISH_TIME DESC
LIMIT 10;
```

**FE deadlock recovery**

```bash
# Confirm FE is Alive and leader is stable
SHOW FRONTENDS;

# Confirm queries succeed (no 'version does not exist')
SELECT 1;  -- simple test query

# Confirm no new LockManager deadlock reports
grep "LockManager\|DeadlockChecker" fe.log | tail -5
```

**FE heap recovery**

```bash
# No Full GC in recent fe.gc.log
grep "Full GC" fe.gc.log | tail -5

# Old Gen occupancy below 70%
grep "Old" fe.gc.log | tail -10

# FE responds within normal latency
SHOW FRONTENDS;
```

---

## Key Configuration Parameters

### FE JVM Configuration

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `-Xmx` | 8g | Maximum JVM heap size | No (restart) |
| `-Xms` | 8g | Initial JVM heap size (set equal to -Xmx) | No (restart) |
| `-XX:+UseG1GC` | false (CMS default in older versions) | Use G1 garbage collector (recommended) | No (restart) |
| `-XX:MaxGCPauseMillis` | 200 | G1GC target max pause time (ms) | No (restart) |
| `-XX:G1HeapRegionSize` | auto | G1 region size — set to 32m for large heaps | No (restart) |
| `-XX:-OmitStackTraceInFastThrow` | omit by default | Include full stack on NullPointerException | No (restart) |
| `proc_profile_cpu_enable` | false | Enable auto memory/CPU profiling | No (restart) |
| `proc_profile_collect_interval_s` | 300 | Profiler capture interval (seconds) | No (restart) |
| `slow_lock_threshold_ms` | 3000 | Lock hold threshold for LockManager slow lock reporting | No (restart) |

### BE Memory Configuration

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `mem_limit` | 80% | BE total memory limit (bytes or % of physical RAM) | No (restart) |
| `query_mem_limit` | 0 (unlimited) | Per-query memory limit on BE | Yes |
| `enable_query_spill` | false | Spill intermediate results to disk when memory limit approached | Yes |
| `spill_mem_table_size` | 256 MB | Memory threshold per operator before spilling | Yes |
| `load_process_max_memory_limit_percent` | 80 | Max % of `mem_limit` usable by load tasks | No (restart) |

---

## Common Issues Quick Reference

| Issue | Cause | Fix |
|---|---|---|
| BE OOM during ingestion | Memory tracker leak; oversized PK index in memory | Upgrade to fixed version; enable persistent index |
| BE crashed without OOM in dmesg | Internal segfault; check core dump | Capture stack; submit to engineering |
| FE Full GC every minute | Heap too small or memory leak | Increase `-Xmx`; check Memory Usage Tracker |
| `version does not exist` errors | FE deadlock blocking tablet report | jstack + restart FE; check DeadLockChecker output |
| FE deadlock - circular wait | Inconsistent database lock ordering | Check version against historical fixes (#29432, #29360) |
| FE deadlock - cross-lock | TabletInvertedIndex vs Database lock ordering | Upgrade to version with fix (#30703) |
| FE deadlock - lock upgrade | Thread holds read lock, tries write lock | Check TabletScheduler fixes (#34237) |
| FE deadlock - thread pool | Shared pool exhausted by nested async tasks | Check Hive meta cache handling |
| FE OOM after long uptime | Slow leak in metadata or task scheduler | Capture allocation profile; report to engineering |

---

## Related Cases

- `case-003-fe-deadlock` — LockManager deadlock blocking ReportHandler
- `case-007-memory-tracking-leak` — Memory tracker leak slowing imports
- `case-008-be-oom` — Publish timeout cascade caused by BE OOM
- `case-021-fe-oom-metadata-operations` — FE OOM due to frequent metadata operations
- `case-022-fe-oom-complex-sql-plan` — FE OOM due to complex SQL plan memory spike
- `case-023-fe-oom-mv-frequent-refresh` — FE OOM due to frequent MV refresh
- `case-024-fe-oom-iceberg-query` — FE OOM due to Iceberg catalog query
- `case-025-fe-oom-insert-memory-leak` — FE OOM due to Insert memory leak
- `case-026-fe-crash-async-profiler` — FE crash due to async-profiler JVM crash
- `case-027-fe-oom-heap-config-insufficient` — FE OOM due to insufficient JVM heap configuration
- `case-029-fe-heap-memory-slow-growth` — FE heap memory slow growth due to tablet delete bug
- `case-030-fe-machine-memory-growth-malloc-arena` — FE machine memory growth due to glibc thread arena
- `case-031-fe-machine-memory-spike-traffic` — FE machine memory spike due to Arrow Flight traffic

---

## Causal Chains

### Chain 1: Large Query OOM → BE Restart → Publish Stall → Cluster-Wide Impact

```
Large query allocates memory beyond BE mem_limit
  ↓ observable: BE log "Memory exceed limit"; process RSS climbs in top/htop
Linux OOM Killer sends SIGKILL to BE process
  ↓ observable: dmesg | grep -i oom shows "Killed process <pid> (starrocks_be)"; BE disappears from FE alive list
In-flight transactions on killed BE cannot complete publish
  ↓ observable: starrocks_be_publish_version_queue_count spikes after BE restarts and rejoins cluster
FE marks tablets as missing; publish waits for replica recovery
  ↓ observable: FE log shows "wait for replica" or clone tasks generated; queries on under-replicated tablets fail
Cluster-wide query and import errors for minutes until replica recovery completes
```
Queries fail with `replica not alive` or return stale results; recovery takes minutes depending on data volume.

**Trigger conditions**: No query memory limit (`query_mem_limit=0`); large ad-hoc analytics on BE with insufficient RAM; `mem_limit` set too close to total physical RAM.

**Break point**: Set `query_mem_limit` to safe fraction of BE memory; enable `enable_query_spill=true`; configure `mem_limit` with OS headroom.

---

### Chain 2: Inconsistent Lock Ordering → FE Deadlock → Tablet Report Frozen → Version Recycled

```
Thread A locks Database-1 then requests Database-2; Thread B does the reverse
  ↓ observable: jstack shows thread A BLOCKED on DB-2 address; thread B BLOCKED on DB-1 address — same two addresses cross-referenced
Circular wait: neither thread can proceed
  ↓ observable: fe.log LockManager / DeadlockChecker emits JSON deadlock report with thread names and lock chain
Tablet report processing thread is one of the blocked threads; version updates freeze
  ↓ observable: fe.log shows no tablet report processing for >60s; SHOW PROC '/statistic' shows report queue growing
FE erroneously recycles tablet versions it believes are stale
  ↓ observable: FE log "version gc" removing versions still needed; subsequent queries hit "version not found"
```
Queries fail with `version does not exist`; DDL operations hang until FE restarted.

**Trigger conditions**: Concurrent rename/swap DDL across two databases; new lock nesting introduced without global ordering.

**Break point**: Enforce canonical lock acquisition order (by DB ID ascending); short-term: FE rolling restart.

---

### Chain 3: FE Heap Exhaustion → Full GC Stop-the-World → Query / Plan Timeout

```
FE heap usage climbs due to large metadata cache or memory leak
  ↓ observable: fe.gc.log shows increasing Old Gen occupancy; GC frequency rising
JVM triggers Full GC; all application threads stop
  ↓ observable: fe.gc.log shows Full GC pause >10s; grep "FE ShutDown! Running Query" fe.WARNING may appear
Query planning and heartbeat threads suspended during pause
  ↓ observable: BE reports FE heartbeat timeout; SHOW FRONTENDS shows FE Alive=false transiently
In-flight query plans not dispatched within timeout window
  ↓ observable: Client receives query timeout or "get query plan failed"; FE log shows plan dispatch latency spike
```
Queries fail sporadically with timeout errors correlated with GC pauses; FE may briefly drop from cluster.

**Trigger conditions**: FE `-Xmx` sized too small for catalog size; metadata cache unbounded growth; memory leak in long-running FE process.

**Break point**: Increase FE `-Xmx` to ≥50% of host RAM; enable G1GC with `MaxGCPauseMillis` target; set catalog cache limits; schedule periodic FE rolling restart.

---

### Chain 4: Hive Meta Thread Pool Exhaustion → Catalog Queries Hang

```
External catalog query triggers async metadata fetch for Hive partitions
  ↓ observable: FE log shows nested async calls to Hive Metastore within catalog thread pool
Each outer task spawns inner tasks that submit to the same bounded pool
  ↓ observable: FE thread dump shows all catalog pool threads WAITING on a future; none available for inner tasks
Pool deadlock: outer tasks wait for inner tasks; inner tasks cannot start (no free thread)
  ↓ observable: jstack shows pool threads parked in ExecutorService with identical stack frames; queue depth grows
All external catalog queries hang indefinitely
```
All Hive/external catalog queries hang; internal table queries unaffected; resolves only after FE restart.

**Trigger conditions**: Hive table with thousands of partitions triggering recursive metadata listing; `hive_meta_fetch_thread_num` pool too small for nested async pattern.

**Break point**: Separate inner (partition listing) tasks onto a dedicated pool; increase `hive_meta_fetch_thread_num`; add partition pruning to avoid full partition listing.

---

## Resources

- [FE Memory FAQ](https://docs.starrocks.io/docs/faq/fe_mem_faq/)
