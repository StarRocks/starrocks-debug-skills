---
type: guide
category: cross-skill
keywords: [cascade, RPC failed, statistics, BRPC, import failure, plan fragment, routine load]
---

# Cross-Skill Guide: Import RPC Failure from Statistics Collection

**Pattern**: Automatic statistics collection → BRPC starvation → plan fragment send failure → import jobs fail

This guide covers a non-obvious cascade where a background maintenance task (statistics collection) silently starves the BRPC thread pool, causing import and query plan delivery failures that appear unrelated to statistics.

---

## The Full Cascade

```
[Trigger] Automatic full statistics collection scheduled (or triggered by first load)
  │         enable_collect_full_statistic = true (default)
  │
  ▼  (import background)
Many concurrent ANALYZE tasks start scanning large tables
  ↓ observable: SELECT * FROM information_schema.task_runs WHERE STATE = 'RUNNING' shows many ANALYZE tasks
  ↓ observable: FE log shows "Statistic collect" tasks starting
  │
  ▼  (import → brpc contention)
ANALYZE scan fragments consume BRPC worker threads on BE nodes
  ↓ observable: curl http://<be>:8060/vars | grep "brpc.*worker" shows active ≈ total threads
  ↓ observable: BRPC latency metrics rising: latency-avg / latency-99 for exec_plan_fragment
  │
  ▼  (import fails)
New plan fragment delivery for import jobs cannot acquire BRPC threads
  ↓ observable: BE log "plan fragment send fail" entries appearing
  ↓ observable: FE log "RPC Failed" when sending fragments to BE for import tasks
  │
  ├─▶ (import) Broker Load / Insert Into jobs fail with "RPC Failed"
  │     ↓ observable: information_schema.loads shows STATE = FAILED; error_msg contains "RPC Failed"
  │     ↓ observable: SHOW LOAD shows CANCELLED jobs during the statistics window
  │
  └─▶ (query) Query plan fragment delivery also fails → user queries fail
        ↓ observable: User queries return "Failed to send fragment" or timeout
        ↓ observable: SHOW PROC '/current_queries' shows queries stuck in PENDING or failing instantly
```

---

## Diagnosis Sequence

### Step 1: Confirm Statistics Collection Is Running

```sql
-- Check currently running statistics tasks
SELECT TASK_NAME, STATE, START_TIME, FINISH_TIME, ERROR_MESSAGE
FROM information_schema.task_runs
WHERE STATE IN ('RUNNING', 'PENDING')
ORDER BY START_TIME DESC LIMIT 20;

-- Check statistics collection config
ADMIN SHOW FRONTEND CONFIG LIKE "%statistic%";
```

- Many RUNNING ANALYZE tasks during import failure window → statistics collection is the trigger
- `enable_collect_full_statistic = true` + `statistic_collect_concurrency` > 1 → high probability

### Step 2: Confirm BRPC Is Saturated

```bash
# Check BRPC worker thread utilization on each BE
for be in be1 be2 be3; do
  echo "=== $be ==="
  curl -s http://$be:8060/vars | grep -E "brpc_(worker|connection)" | head -5
done

# Check plan fragment RPC latency
curl -s http://<be>:8060/vars | grep "exec_plan_fragment" | grep -E "latency|count"
```

- `brpc_worker_thread_count_active ≈ brpc_worker_thread_count_total` → BRPC saturated
- `exec_plan_fragment` latency-99 >> normal (>1s) → server-side processing slow

### Step 3: Correlate Timestamps

```bash
# In FE log: find "RPC Failed" entries and check surrounding context
grep "RPC Failed\|Statistic collect\|ANALYZE" fe.log | grep "$(date +%Y%m%d)" | head -50

# Check if failures cluster around statistics collection start times
grep "Statistic collect task start" fe.log | tail -10
grep "RPC Failed" fe.log | tail -10
```

- "RPC Failed" entries appear within minutes of "Statistic collect task start" → confirmed correlation

### Step 4: Check TCP Connection Health (Rule Out Network)

```bash
# Check for TCP-level issues that could look like BRPC saturation
netstat -na | grep 8060 | awk '{print $6}' | sort | uniq -c
# Large number of CLOSE_WAIT or TIME_WAIT → connection lifecycle issue

# Check for packet loss / retransmits
netstat -s | grep -i "retransmit\|failed\|error" | head -10
```

- No TCP anomalies → BRPC thread saturation confirmed (not network)
- TCP anomalies present → check network separately; may be compounding factor

---

## Immediate Mitigation

### Option A: Disable Statistics Collection (Fastest)

```sql
-- Disable during active import window
ADMIN SET FRONTEND CONFIG ("enable_collect_full_statistic" = "false");
ADMIN SET FRONTEND CONFIG ("enable_statistic_collect" = "false");
ADMIN SET FRONTEND CONFIG ("enable_statistic_collect_on_first_load" = "false");
```

Wait ~2 minutes for in-flight ANALYZE tasks to finish, then retry failed import jobs.

### Option B: Throttle Statistics Concurrency (Less Disruptive)

```sql
-- Reduce concurrent statistics tasks without fully disabling
ADMIN SET FRONTEND CONFIG ("statistic_collect_concurrency" = "1");
ADMIN SET FRONTEND CONFIG ("statistic_collect_interval_sec" = "1200");
```

### Option C: Increase BRPC Worker Threads (Adds Capacity)

In `be.conf` (requires BE restart or dynamic config if supported):
```
brpc_worker_threads = <2x current value>
```

---

## Root Fix and Prevention

| Problem | Fix |
|---|---|
| Statistics collection conflicts with import peak hours | Schedule statistics collection during off-peak (e.g., 2–4 AM) via `statistic_collect_interval_sec` |
| BRPC thread count too low | Set `brpc_worker_threads` ≥ (CPU cores × 2) |
| Concurrency too high | Set `statistic_collect_concurrency = 1` |
| First-load statistics triggers on every new table | Set `enable_statistic_collect_on_first_load = false` for bulk loading workflows |
| Sampling threshold too low | Set `statistic_max_full_collect_data_size` to a larger value so large tables use sampling instead of full scan |

---

## Secondary Cascade: Routine Load Lag After Recovery

If Routine Load jobs were active during the BRPC failure window:

```
BRPC failure → Routine Load task cannot deliver plan fragment
  → Task reported as FAILED; Routine Load job marks task as abnormal
    → Job pauses and restarts task consumption from last committed offset
      → Consumer group lag grows during downtime
        → After recovery, burst consume to catch up
          → Burst import rate may exceed compaction throughput
            → Triggers compaction cascade (see cascade-cluster-degradation.md)
```

**Prevention**: Set `max_routine_load_batch_size` to a moderate value to prevent burst catch-up from overwhelming storage.

---

## Monitoring Alerts to Add

| Metric | Condition | Action |
|---|---|---|
| `brpc_worker_thread_count_active / brpc_worker_thread_count_total` | > 0.85 for >30s | Reduce statistics concurrency |
| `exec_plan_fragment` latency-99 | > 500ms | Investigate BRPC contention |
| `information_schema.task_runs` RUNNING count | > 5 | Check if statistics collection is overlapping with import |
| Routine Load consumer group lag | Growing for >5 min | Check Routine Load task health |

---

## Related Skills and Cases

| Topic | Reference |
|---|---|
| RPC Failed diagnosis | [import/SKILL.md](../import/SKILL.md) — Section 13 |
| Statistics collection tuning | [import/SKILL.md](../import/SKILL.md) — Section 13 |
| Routine Load lag | [import/SKILL.md](../import/SKILL.md) — Chain 5 |
| Compaction cascade (post-recovery) | [guides/cascade-cluster-degradation.md](./cascade-cluster-degradation.md) |
| RPC Failed case | [import/references/case-002-rpc-failed-statistics.md](../import/references/case-002-rpc-failed-statistics.md) |
