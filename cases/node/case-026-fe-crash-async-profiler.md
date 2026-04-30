---
type: case
category: node
issue: fe-crash-async-profiler
keywords: [fe, crash, jvm, async-profiler, hs_err_pid, signal-handler, perf-events, cpu-profile]
---

# Case-026: FE Crash Due to async-profiler JVM Crash

## Environment

- StarRocks version: Enterprise version (all versions)
- Architecture: shared-nothing / shared-data

## Symptom

**Symptoms observed:**
- FE process crashes or restarts unexpectedly
- Log directory contains `hs_err_pid$pid.log` file

## Investigation

### Step 1: Check for JVM Crash Log

```bash
# Look for crash log in FE log directory
ls -la fe.log.* hs_err_pid*.log
```

If `hs_err_pid$pid.log` file exists, FE crash may be caused by JVM crash.

### Step 2: Analyze Crash Log

Crash log summary section:

```
---------------  S U M M A R Y ------------
Command Line: -Djava.security.krb5.conf=/etc/krb5.conf -Dlog4j2.formatMsgNoLookups=true -Xmx32768m -XX:+UseG1GC ...
Host: Intel(R) Xeon(R) Gold 5218 CPU @ 2.30GHz, 32 cores, 62G, Anolis OS release 8.6
Time: Sat Jun 28 07:37:05 2025 CST elapsed time: 2305362.708420 seconds (26d 16h 22m 42s)
---------------  T H R E A D  ---------------
Current thread (0x00007f934405f000):  JavaThread "tablet scheduler" daemon [_thread_in_Java, id=164560, ...]
...
Native frames: (J=compiled Java code, A=aot compiled Java code, j=interpreted, Vv=VM code, C=native code)
V  [libjvm.so+0x79cccd]  frame::entry_frame_is_first() const+0xd
V  [libjvm.so+0x285da8]  forte_fill_call_trace_given_top(JavaThread*, ASGCT_CallTrace*, int, frame) [clone .isra.20]+0x3e4
V  [libjvm.so+0x79c678]  AsyncGetCallTrace+0x188
C  [libasyncProfiler.so+0x16f79]  Profiler::getJavaTraceAsync(void*, ASGCT_CallFrame*, int, StackContext*)+0xe9
C  [libasyncProfiler.so+0x352e2]  Profiler::recordSample(void*, unsigned long long, int, Event*)+0x232
C  [libasyncProfiler.so+0x35924]  PerfEvents::signalHandler(int, siginfo*, void*)+0x74
```

**Key findings:**
- Crash point: `PerfEvents::signalHandler`
- Cause: async-profiler triggering JVM internal segment fault
- async-profiler is a performance analysis tool
- Crash occurred while attempting to get Java call stack

### Step 3: Verify async-profiler Configuration

Enterprise version deploys async-profiler by default to periodically collect process information.

## Root Cause

**async-profiler JVM crash:**

1. **async-profiler**: Performance analysis tool deployed in enterprise version
2. **Signal handler crash**: async-profiler triggers JVM segment fault during call stack capture
3. **PerfEvents**: CPU profiling signal handler causes crash
4. **JVM bug**: Interaction between async-profiler and JVM causes instability

## Resolution

### Disable async-profiler CPU Collection

**Option 1: FE configuration command**

```sql
ADMIN SET FRONTEND CONFIG ("proc_profile_cpu_enable" = "false");
```

**Option 2: fe.conf configuration**

```
# Add to fe.conf
proc_profile_cpu_enable=false
```

## Key Commands

```bash
# Check for crash log
ls -la fe.log.* hs_err_pid*.log

# Analyze crash log
head -50 hs_err_pid*.log

# Check async-profiler related frames
grep -A5 "libasyncProfiler" hs_err_pid*.log
```

```sql
-- Disable CPU profiling
ADMIN SET FRONTEND CONFIG ("proc_profile_cpu_enable" = "false");

-- Check frontend config
ADMIN SHOW FRONTEND CONFIG LIKE 'proc_profile_cpu_enable';
```

## Lessons Learned

1. **Crash log detection**: `hs_err_pid*.log` indicates JVM crash
2. **async-profiler risk**: Can cause JVM instability in certain scenarios
3. **Enterprise version**: Be aware of additional profiling tools
4. **Configuration management**: Disable problematic profiling features proactively

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [case-003-fe-deadlock.md](case-003-fe-deadlock.md) — FE deadlock