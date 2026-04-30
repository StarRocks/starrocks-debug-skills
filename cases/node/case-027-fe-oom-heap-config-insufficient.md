---
type: case
category: node
issue: fe-oom-heap-config-insufficient
keywords: [fe, oom, heap, jvm, xmx, crash, leader-switch, metadata-recovery, bdbje]
---

# Case-027: FE OOM Due to Insufficient JVM Heap Configuration

## Environment

- StarRocks version: All versions
- Architecture: shared-nothing / shared-data
- FE configuration: 3 FE nodes (typical)

## Symptom

**Scenario:**
- 3 FE nodes in cluster
- Leader crashes due to insufficient memory
- Normally, remaining 2 followers should elect new leader and continue service
- If the 2 followers cannot communicate normally after leader crash, the follower preparing to switch to leader may also exit due to insufficient metadata replicas

**Symptoms observed:**
- 2 FE crashes occur

## Investigation

### Step 1: Check FE Leader Log

```
java.lang.OutOfMemoryError: Java heap space
2025-08-09 08:22:30.926-06:00 WARN (thrift-server-pool-9443633|65019425) [TIOStreamTransport.close():153] Error closing output stream.
java.net.SocketException: Socket closed
...
2025-08-09 08:22:30.927-06:00 ERROR (thrift-server-accept|144) [SRTThreadPoolServer.execute():221] ExecutorService threw error: java.lang.OutOfMemoryError: Java heap space
java.lang.OutOfMemoryError: Java heap space
```

**Key findings:**
- Leader crashed due to OOM: `Java heap space`
- Planner error due to long memo phase time (3000ms)
- Possible causes: FE Full GC, Hive external table metadata fetch delay, complex SQL

### Step 2: Check Follower Crash Log

```
2025-08-09 08:16:07.844-06:00 ERROR (stateChangeExecutor|86) [GlobalStateMgr.transferToLeader():1333] failed to init journal after transfer to leader! will exit
com.starrocks.journal.JournalException: catch exception after retried 3 times
    at com.starrocks.journal.bdbje.BDBJEJournal.open(BDBJEJournal.java:222)
    at com.starrocks.server.GlobalStateMgr.transferToLeader(GlobalStateMgr.java:1323)
Caused by: com.sleepycat.je.rep.InsufficientReplicasException: (JE 18.3.20) Commit policy: SIMPLE_MAJORITY required 1 replica. But none were active with this master.
```

**Key findings:**
- Follower fails to transfer to leader
- BDBJE journal initialization fails
- `InsufficientReplicasException`: No active replicas for commit

### Step 3: Check Shutdown Hooks

```
2025-08-09 08:16:07.852-06:00 WARN (Thread-70|2103846) [ConnectScheduler.lambda$printAllRunningQuery$4():339] FE ShutDown! Running Query:show frontends;,  QueryFEAllocatedMemory: 69224
...
```

## Root Cause

**Insufficient JVM heap configuration:**

1. **Leader OOM**: Heap memory too small for workload
2. **Follower crash**: After leader crash, follower cannot communicate with other replicas
3. **BDBJE issue**: Metadata replication needs majority, insufficient replicas cause leader transfer failure
4. **Cascading failure**: One OOM can trigger cascading FE crashes if communication issues exist

## Resolution

### Increase FE JVM Heap Configuration

**Production recommendation:**
- Minimum: 16GB heap
- Adjust based on metadata growth

```
# fe.conf configuration
-Xmx16384m  # Minimum recommended for production
```

### Recovery Procedure

Refer to official documentation:
https://docs.mirrorship.cn/zh/docs/administration/Meta_recovery/#9-unknownmasterexception

**Key steps (8 and 9):**

1. Stop all FE nodes
2. Check metadata consistency
3. Recover leader from latest metadata
4. Restart follower nodes
5. Verify cluster health

## Key Commands

```bash
# Check FE JVM heap configuration
cat fe.conf | grep Xmx

# Check GC status
jstat -gcutil <FE_pid> 1000 10

# Check memory usage
curl http://fe_host:8030/metrics | grep -i memory
```

```sql
-- Check frontends status
SHOW FRONTENDS;
```

## Lessons Learned

1. **Minimum heap size**: Production FE should have at least 16GB heap
2. **Metadata growth**: Adjust heap size based on metadata volume growth
3. **Cascading failure**: OOM can cause multiple FE crashes if communication issues exist
4. **Recovery procedure**: Follow official documentation for metadata recovery

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [08-deployment.md](../../skills/08-deployment.md) — Deployment and configuration