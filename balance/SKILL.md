---
name: balance
description: "Use when the tablet scheduler is unhealthy: replicas are missing, clone tasks are failing or timing out, balance is too slow (disk usage skewed) or too aggressive (impacting imports), BE decommission is stuck, or colocate groups are unstable. Covers SHOW PROC /cluster_balance analysis, fe_tablet_schedules system table, clone slot tuning, and decommission unblocking via recycle-bin and replica count checks."
version: 2.0.0
category: balance
keywords:
- balance
- tablet scheduler
- clone
- repair
- decommission
- colocate
- replica missing
- disk balance
tools:
- find
related_cases:
- case-004-disk-balancing
---

# Balance & Tablet Scheduler Troubleshooting

Investigation guide for tablet scheduling, cluster balance, replica repair, and
decommission issues.

Five root causes account for the vast majority of cases:

- **Cause A** — balance throughput too low (clone tasks not progressing)
- **Cause B** — balance too aggressive (impacting writes and queries)
- **Cause C** — decommission BE stuck (replicas cannot be relocated)
- **Cause D** — clone task repeated failure (destination path or disk issues)
- **Cause E** — colocate group unstable (group marked Unstable, balance loops)

---

## Metric Taxonomy — Read This First

Before using any metrics, understand the two-layer structure:

### FE-level metrics (aggregated, cross-node)

| Metric name | Meaning |
|---|---|
| `starrocks_fe_scheduled_tablet_num` | Total tablets currently being scheduled (pending + running) |
| `starrocks_fe_scheduled_pending_tablet_num{type="BALANCE"}` | Tablets waiting for a balance clone slot |
| `starrocks_fe_scheduled_pending_tablet_num{type="REPAIR"}` | Tablets waiting for a repair clone slot |
| `starrocks_fe_scheduled_running_tablet_num{type="BALANCE"}` | Balance clone tasks actively running |
| `starrocks_fe_scheduled_running_tablet_num{type="REPAIR"}` | Repair clone tasks actively running |
| `starrocks_fe_clone_task_total` | Cumulative clone tasks submitted |
| `starrocks_fe_clone_task_success` | Cumulative clone tasks completed successfully |
| `starrocks_fe_clone_task_copy_duration_ms{type="INTER_NODE"}` | Clone task copy duration for inter-node balance |
| `starrocks_fe_clone_task_copy_bytes{type="INTER_NODE"}` | Bytes copied per clone task |

### BE-level metrics (per-node)

| Metric name | Meaning |
|---|---|
| `starrocks_be_clone_task_copy_bytes{type="INTER_NODE"}` | Bytes transferred per clone on this BE |
| `starrocks_be_clone_task_copy_duration_ms{type="INTER_NODE"}` | Clone duration on this BE |
| `starrocks_be_engine_requests_total{status="failed",type="clone"}` | Failed clone requests on this BE |

### SQL observability

Key `SHOW PROC "/statistic"` fields:

| Field | Meaning |
|---|---|
| `UnhealthyTabletNum` | Tablets with missing replicas, incomplete versions, colocate mismatch, or location mismatch |
| `InconsistentTabletNum` | Tablets with data inconsistency across replicas |
| `CloningTabletNum` | Tablets currently being cloned |
| `ErrorStateTabletNum` | Tablets in error state (PK tables only) |

Key `SHOW PROC "/cluster_balance"` sub-paths:

| Sub-path | Meaning |
|---|---|
| `cluster_load_stat` | Disk usage and tablet count per BE by medium type |
| `working_slots` | Available/total clone slots per BE path |
| `sched_stat` | Scheduler statistics snapshot (updated every 20s) |
| `priority_repair` | Tablets needing priority repair |
| `pending_tablets` | Tablets waiting for scheduling |
| `running_tablets` | Tablets currently being cloned |
| `history_tablets` | Recently completed tablets (max 1000, includes ErrMsg) |
| `balance_stat` | Balance overview by balance type (3.5+) |

### How to retrieve metrics

**Option 1 — curl the FE HTTP port (immediate, no Prometheus needed)**

```bash
# Pull all balance-related metrics from FE
curl -s "http://<fe_ip>:8030/metrics" | grep -E "scheduled_tablet|clone_task"

# Pending/running breakdown by type
curl -s "http://<fe_ip>:8030/metrics" | grep "scheduled_pending_tablet_num\|scheduled_running_tablet_num"
```

**Option 2 — curl the BE HTTP port (per-node clone health)**

```bash
# Clone failures on a specific BE
curl -s "http://<be_ip>:8040/metrics" | grep -E "clone_task|engine_requests.*clone"

# Loop across all BEs
for be in 10.0.0.1 10.0.0.2 10.0.0.3; do
  echo "=== $be ==="
  curl -s "http://$be:8040/metrics" | grep "engine_requests_total.*clone"
done
```

**Option 3 — SQL (primary method for balance diagnosis)**

```sql
-- Cluster-wide unhealthy tablet summary
SHOW PROC "/statistic";

-- Unhealthy tablets in a specific database
SHOW PROC "/statistic/<db_id>";

-- Balance overview
SHOW PROC "/cluster_balance";
SHOW PROC "/cluster_balance/sched_stat";
SHOW PROC "/cluster_balance/working_slots";
SHOW PROC "/cluster_balance/pending_tablets";
SHOW PROC "/cluster_balance/running_tablets";
SHOW PROC "/cluster_balance/history_tablets";

-- Disk usage per BE (v3.5+)
SHOW PROC "/cluster_balance/balance_stat";
SHOW PROC "/cluster_balance/cluster_load_stat/HDD";
SHOW PROC "/cluster_balance/cluster_load_stat/SSD";

-- Clone task history for a specific table
SELECT * FROM information_schema.fe_tablet_schedules
WHERE table_id = <table_id>
ORDER BY create_time DESC LIMIT 50;

-- Find partitions with imbalanced tablet distribution
SELECT partition_id, MAX(cnt) AS max_cnt, MIN(cnt) AS min_cnt,
       MAX(cnt) - MIN(cnt) AS diff
FROM (
  SELECT partition_id, be_id, COUNT(*) AS cnt
  FROM information_schema.be_tablets
  GROUP BY partition_id, be_id
) t
GROUP BY partition_id
HAVING diff > 1
ORDER BY diff DESC LIMIT 20;

-- Tables with replication_num > 2 (relevant for decommission)
SELECT db_name, table_name, partition_name, replication_num
FROM information_schema.partitions_meta
WHERE replication_num > 2;
```

**Default HTTP ports**: BE = 8040, FE = 8030. Verify with `SHOW BACKENDS` or `be.conf` / `fe.conf`.

---

**Key rules for interpretation**:

1. `UnhealthyTabletNum > 0` is the primary alert signal. Drill into `SHOW PROC "/statistic/<db_id>"` to identify which databases are affected.
2. `CloningTabletNum` > 0 with `UnhealthyTabletNum` decreasing means repair is in progress — expected behavior. Only investigate if `CloningTabletNum` is stuck.
3. `starrocks_fe_clone_task_success` should be rising. If `clone_task_total` rises but `clone_task_success` does not, clone tasks are failing — check `history_tablets.ErrMsg`.
4. Disk usage difference > 10% between BEs (`SHOW BACKENDS`: `DataUsedCapacity`) triggers disk balance clones automatically.
5. `working_slots` all zero or all full: zero means no work; all full means scheduler is at capacity — check if tablets are actually moving by watching `CloningTabletNum`.

---

## Balance Health Reference

| Metric / Phenomenon | Healthy | Needs Attention | Act Immediately |
|---|---|---|---|
| `UnhealthyTabletNum` | 0 | 1–100 (and decreasing) | > 100 sustained or not decreasing |
| `CloningTabletNum` | 0 (no activity) or rising then falling | Stuck at same value > 10 min | Stuck with ErrMsg in history_tablets |
| Disk usage difference between BEs | < 10% | 10–20% | > 20% (balance triggered automatically) |
| Tablet count difference between BEs (same partition) | 0–1 | 2–5 | > 5 (imbalanced scans) |
| `clone_task_total` rate vs `clone_task_success` rate | Equal | Success rate < 90% | Success rate < 50% |
| `working_slots` fill rate | 20–80% | > 95% (saturated) | 100% with tasks still pending > 30 min |

---

## Phase 1 — Confirm Balance / Repair Issue

### Step 1.1 — Quick cluster health signal

```sql
-- Run on Leader FE
SHOW PROC "/statistic";
```

Key interpretation:
- `UnhealthyTabletNum = 0` across all DBs → cluster is healthy, no action needed
- `UnhealthyTabletNum > 0` → proceed to Step 1.2
- `CloningTabletNum > 0` → repair/balance is in progress; check if it's making progress

### Step 1.2 — Classify the issue type

```sql
SHOW PROC "/cluster_balance/sched_stat";
```

Check the `Increase` column to see if counters are advancing:

| Observation | Points toward |
|---|---|
| `num of balance scheduled` rising, `UnhealthyTabletNum` shrinking | Balance is working; just slow — Cause A |
| `num of clone task failed` rising | Clone tasks failing — Cause D |
| `num of replica missing error` high but no clone tasks starting | Scheduler paused or slots exhausted |
| `num of colocate replica mismatch` non-zero and not decreasing | Cause E — colocate group unstable |
| Large pending_tablets queue, few running_tablets | Cause A — throughput too low |
| `running_tablets` saturated, queries/writes slow | Cause B — balance too aggressive |

```sql
-- Drill into pending and running queues
SHOW PROC "/cluster_balance/pending_tablets";
SHOW PROC "/cluster_balance/running_tablets";

-- Check slot utilization per path
SHOW PROC "/cluster_balance/working_slots";
```

### Step 1.3 — Identify which tablets are problematic

```sql
-- Clone task history with error messages
SHOW PROC "/cluster_balance/history_tablets";

-- Or query system table for specific table
SELECT tablet_id, type, state, schedule_reason, priority,
       failed_schedule_count, failed_running_count, msg
FROM information_schema.fe_tablet_schedules
WHERE table_id = <table_id>
ORDER BY create_time DESC LIMIT 20;
```

---

## Phase 2 — Locate and Characterize

### Step 2.1 — Identify issue type from Step 1.2

#### Cause A — Balance too slow

**Confirm all match**:
- `pending_tablets` queue large (many tablets waiting)
- `working_slots` shows low utilization (slots available but few tasks running)
- `UnhealthyTabletNum` not decreasing over 10+ minutes
- No error messages in `history_tablets`

**Mechanism**: The tablet scheduler submits clone tasks into slots — `tablet_sched_slot_num_per_path` per BE path. If slots are configured conservatively (default 8) and disks are fast, the scheduler underutilizes available IO, leaving balance tasks in the pending queue far longer than necessary. Each clone copies a full tablet replica from source to destination.

→ **Go to Phase 3, Cause A**

#### Cause B — Balance too active (impacts queries/writes)

**Confirm all match**:
- `working_slots` fill rate near 100%
- `running_tablets` at or near `tablet_sched_max_scheduling_tablets` limit
- Query latency elevated; load job write latency increased
- `iostat` on affected BEs shows disk IO > 80%

**Mechanism**: Clone tasks perform sequential read on source and sequential write on destination. When many clones run concurrently, they compete with query scan IO and load write IO for disk bandwidth. The scheduler has no IO-awareness — it will fill all slots regardless of disk pressure.

→ **Go to Phase 3, Cause B**

#### Cause C — Decommission BE stuck

**Confirm all match**:
- FE leader log shows: `backend <id> lefts N replicas to decommission`
- `SHOW BACKENDS` shows BE in `DECOMMISSIONING` state for > 30 minutes
- `history_tablets` shows `REPLICA_RELOCATING` tasks failing

**Mechanism**: Decommission moves all replicas off the BE. It can block when: (1) remaining BEs cannot satisfy replication factor (e.g., 3 BEs remaining with 3-replica tables); (2) tablets exist in FE recycle bin holding replica references; (3) statistics system tables have fixed 3-replica config.

→ **Go to Phase 3, Cause C**

#### Cause D — Clone task repeated failure

**Confirm all match**:
- `history_tablets.ErrMsg` contains `unable to find dest path` or similar
- `failed_running_count > 3` for specific tablets in `fe_tablet_schedules`
- `starrocks_be_engine_requests_total{status="failed",type="clone"}` rising on specific BE

**Mechanism**: Clone tasks fail when the destination BE has no path with sufficient free space, the destination BE is down or unreachable, or the source tablet is in error state. Repeated failures cause the tablet to cycle in the pending queue indefinitely, keeping `UnhealthyTabletNum` elevated.

→ **Go to Phase 3, Cause D**

#### Cause E — Colocate group unstable

**Confirm all match**:
- `SHOW PROC "/colocation_group"` shows group in `Unstable` status
- `num of colocate replica mismatch` in `sched_stat` is non-zero and not clearing
- Group was recently affected by BE failure or BE addition

**Mechanism**: Colocate groups require tablets to be co-located on specific BEs as defined by the bucket sequence. When a BE fails or is added, the group is marked Unstable and the scheduler starts relocating tablets to restore the required co-location pattern. Until the group is Stable again, colocate joins degrade to regular joins.

→ **Go to Phase 3, Cause E**

---

## Phase 3 — Take Action by Cause

### Cause A — Balance too slow

**Step A-1: Increase scheduler throughput (dynamic, no restart)**

```sql
-- FE: more slots per path (default 8 → 16)
ADMIN SET FRONTEND CONFIG ("tablet_sched_slot_num_per_path" = "16");

-- FE: allow more total concurrent scheduling tablets (default 1000 → 2000)
ADMIN SET FRONTEND CONFIG ("tablet_sched_max_scheduling_tablets" = "2000");

-- FE: allow more balance tasks (default 500 → 1000)
ADMIN SET FRONTEND CONFIG ("tablet_sched_max_balancing_tablets" = "1000");
```

**Step A-2: Increase BE clone parallelism (requires be.conf edit + restart per BE)**

```ini
# be.conf
parallel_clone_task_per_path = 16   # default 8
```

**Step A-3: Trigger priority repair for specific tables**

```sql
-- Set high-priority repair (faster scheduling)
ADMIN REPAIR TABLE <table_name>;
ADMIN REPAIR TABLE <table_name> PARTITION (p1, p2);
```

**Step A-4: Monitor progress**

```sql
-- Watch CloningTabletNum and UnhealthyTabletNum
SHOW PROC "/statistic";

-- Clone task success rate
SELECT * FROM information_schema.fe_tablet_schedules
WHERE state = 'FINISHED'
ORDER BY finish_time DESC LIMIT 20;
```

---

### Cause B — Balance too active (impacts other tasks)

**Step B-1: Reduce concurrency immediately**

```sql
-- Reduce slots per path (default 8 → 4)
ADMIN SET FRONTEND CONFIG ("tablet_sched_slot_num_per_path" = "4");

-- Cap balance tasks (default 500 → 200)
ADMIN SET FRONTEND CONFIG ("tablet_sched_max_balancing_tablets" = "200");
```

**Step B-2: Clear the queue if needed**

```sql
-- Remove all pending and running tasks
ALTER SYSTEM CLEAN TABLET SCHEDULER QUEUE;
```

**Step B-3: Optionally disable balance temporarily**

```sql
-- Disable non-colocate balance (repair still runs)
ADMIN SET FRONTEND CONFIG ("tablet_sched_disable_balance" = "true");

-- Re-enable after import window
ADMIN SET FRONTEND CONFIG ("tablet_sched_disable_balance" = "false");
```

**Step B-4: Throttle clone bandwidth (if BE supports it)**

```sql
-- Limit clone speed per task in MB/s
ADMIN SET FRONTEND CONFIG ("max_clone_task_speed_mbps" = "50");
```

---

### Cause C — Decommission BE stuck

**Step C-1: Diagnose the blocker**

```bash
# Check FE leader log for remaining replicas
grep "lefts.*replicas to decommission" fe.log | tail -20
```

```sql
-- Find tables with replication_num > 2 (may block decommission if only 3 BEs remain)
SELECT db_name, table_name, partition_name, replication_num
FROM information_schema.partitions_meta
WHERE replication_num > 2;

-- Check history_tablets for REPLICA_RELOCATING failures
SHOW PROC "/cluster_balance/history_tablets";
```

**Step C-2: Handle recycle bin tablets**

```sql
-- If tablets show null db/table/partition — they are in the recycle bin
-- Shorten trash expiry to accelerate cleanup
ADMIN SET FRONTEND CONFIG ("catalog_trash_expire_second" = "60");
```

**Step C-3: Handle 3-replica tables with minimum BEs**

```sql
-- Option 1: Temporarily reduce replication factor
ALTER TABLE <table_name> SET ("replication_num" = "2");
-- After decommission completes:
ALTER TABLE <table_name> SET ("replication_num" = "3");

-- Option 2: Add another BE to the cluster first
```

**Step C-4: Verify decommission is progressing**

```sql
-- Remaining tablets should decrease
SHOW BACKENDS;
-- Check StorageUsedCapacity on decommissioning BE — should approach 0
```

---

### Cause D — Clone task repeated failure

**Step D-1: Identify the error**

```sql
-- Check error messages
SELECT tablet_id, type, failed_running_count, msg
FROM information_schema.fe_tablet_schedules
WHERE failed_running_count > 2
ORDER BY failed_running_count DESC LIMIT 20;

-- Check history_tablets for recent failures
SHOW PROC "/cluster_balance/history_tablets";
```

**Step D-2: Fix based on error type**

```
Error: "unable to find dest path for new replica"
→ Check disk space on all BEs: SHOW BACKENDS (DataUsedCapacity vs TotalCapacity)
→ If disk full: free space or add BE
→ Check that destination BE is alive and heartbeating

Error: "clone task timeout"
→ Increase timeout (dynamic):
```

```sql
ADMIN SET FRONTEND CONFIG ("tablet_sched_max_clone_task_timeout_sec" = "7200");
```

```
Error: source replica in error state
→ Mark replica bad to trigger fresh clone from healthy replica:
```

```sql
ADMIN SET REPLICA STATUS PROPERTIES(
  "tablet_id" = "<id>",
  "backend_id" = "<bad_be_id>",
  "status" = "bad"
);
```

**Step D-3: Restore replica if over-marked**

```sql
ADMIN SET REPLICA STATUS PROPERTIES(
  "tablet_id" = "<id>",
  "backend_id" = "<be_id>",
  "status" = "ok"
);
```

---

### Cause E — Colocate group unstable

**Step E-1: Check group state**

```sql
-- List all colocate groups and their status
SHOW PROC "/colocation_group";

-- Check specific group balance
SHOW PROC "/colocation_group/<group_id>";
```

**Step E-2: Check tolerate time configuration**

```sql
-- Default 12hr: colocate repair does not start until BE is down this long
-- Reduce if you want faster recovery (but risks unnecessary moves on short outages)
ADMIN SET FRONTEND CONFIG ("tablet_sched_colocate_be_down_tolerate_time_s" = "3600");

-- Wait time before starting balance after system stabilizes (default 15min)
ADMIN SET FRONTEND CONFIG ("tablet_sched_colocate_balance_wait_system_stable_time_s" = "300");
```

**Step E-3: Wait or force**

```sql
-- If all BEs are healthy, the group should auto-stabilize
-- Monitor:
SHOW PROC "/cluster_balance/sched_stat";
-- Watch "num of colocate replica mismatch" decreasing
```

---

## Phase 4 — Verify Recovery

```sql
-- Primary signal: UnhealthyTabletNum should reach 0
SHOW PROC "/statistic";

-- Clone tasks completing, none stuck
SHOW PROC "/cluster_balance/sched_stat";
-- Confirm: "num of clone task succeeded" rising, "num of clone task failed" stable

-- History confirms clean completions
SHOW PROC "/cluster_balance/history_tablets";
-- Status column should show FINISHED, not FAILED

-- Verify specific table recovery
SELECT tablet_id, type, state, msg
FROM information_schema.fe_tablet_schedules
WHERE table_id = <table_id>
  AND state != 'FINISHED'
ORDER BY create_time DESC;
```

```bash
# Grafana: starrocks_fe_scheduled_pending_tablet_num should trend to 0
# Clone success rate: starrocks_fe_clone_task_success / starrocks_fe_clone_task_total → 1.0
```

Target state:
- `UnhealthyTabletNum = 0` across all databases
- `CloningTabletNum = 0`
- No `REPLICA_MISSING` or `VERSION_INCOMPLETE` entries in `pending_tablets`

---

## Key Configuration Parameters

### FE Configuration

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `tablet_sched_slot_num_per_path` | 8 | Clone slots per BE path | Yes |
| `tablet_sched_max_scheduling_tablets` | 1000 | Max concurrent scheduling tablets (pending+running) | Yes |
| `tablet_sched_max_balancing_tablets` | 500 | Max tablets in balance queue | Yes |
| `tablet_sched_max_not_be_scheduled_interval_ms` | 15min | Time before tablet priority is upgraded | Yes |
| `tablet_sched_min_clone_task_timeout_sec` | 180 (3min) | Minimum clone timeout | Yes |
| `tablet_sched_max_clone_task_timeout_sec` | 7200 (2hr) | Maximum clone timeout | Yes |
| `tablet_sched_disable_balance` | false | Disable non-colocate balance | Yes |
| `tablet_sched_disable_colocate_balance` | false | Disable colocate balance | Yes |
| `tablet_sched_balance_load_score_threshold` | 0.1 | Disk usage difference threshold to trigger balance | Yes |
| `tablet_sched_balance_load_disk_safe_threshold` | 0.5 | Max disk usage allowed for clone destination | Yes |
| `tablet_sched_checker_interval_seconds` | 20 | Tablet health check interval | Yes |
| `tablet_sched_be_down_tolerate_time_s` | 900 (15min) | BE down time before repair clone starts | Yes |
| `tablet_sched_colocate_be_down_tolerate_time_s` | 43200 (12hr) | BE down tolerance for colocate repair | Yes |
| `tablet_sched_repair_delay_factor_second` | 60 | Repair delay multiplier (HIGH=1x, NORMAL=2x, LOW=3x) | Yes |
| `tablet_sched_colocate_balance_wait_system_stable_time_s` | 900 (15min) | Wait after BE event before colocate balance starts | Yes |
| `catalog_trash_expire_second` | 86400 | Recycle bin TTL; reduce to unblock decommission | Yes |
| `max_clone_task_speed_mbps` | 0 (unlimited) | Per-clone bandwidth cap in MB/s | Yes |

### BE Configuration

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `parallel_clone_task_per_path` | 8 | Concurrent clone threads per disk path | No (restart) |

---

## Causal Chains

### Chain 1: Disk Capacity Imbalance → Tablet Migration → Clone IO Saturation → Read/Write Slow

```
One or more BE nodes have significantly higher disk utilization than peers
  ↓ observable: SHOW PROC '/cluster_balance' shows imbalanced disk usage; Grafana per-node disk util divergence
Tablet scheduler initiates migration (clone) from high- to low-utilization nodes
  ↓ observable: starrocks_be_clone_task_count rising on source and destination; BE log clone tasks started
Clone tasks generate heavy sequential read/write on involved disks
  ↓ observable: iostat shows disk util > 80% on involved nodes; network throughput between nodes elevated
Normal read and write operations experience latency increase
  ↓ observable: Profile OLAP_SCAN_NODE Active time variance between instances; load job write latency rises
```
Intermittent slow queries and elevated load latency on specific nodes.

**Trigger conditions**: Uneven initial tablet distribution; new node added to cluster; previous node failure leaving survivors with extra tablets.

**Break point**: Throttle clone bandwidth via `max_clone_task_speed_mbps`; reduce concurrent clones via `tablet_sched_max_scheduling_tablets`; schedule rebalancing during off-peak.

---

### Chain 2: Clone Task During PK Import → PK Index Preload → Write Timeout

```
Tablet scheduler triggers clone of PK table tablet during active import
  ↓ observable: starrocks_be_clone_task_count > 0; BE log clone task for PK tablet started
Clone destination BE preloads PK index for the newly cloned tablet
  ↓ observable: BE log PK index preload started; memory usage on destination BE rising; disk read IO for index files
PK index preload competes with import write path for IO and CPU
  ↓ observable: BE log write latency increasing; Grafana import bytes/sec drops on destination node
Import transaction write timeout triggered
  ↓ observable: FE log transaction timeout; load job transitions to CANCELLED
```
Load job failure coincides with rebalance activity.

**Trigger conditions**: Active PK table imports while tablet scheduler runs clone tasks; large PK index relative to available IO bandwidth.

**Break point**: Set `skip_pk_preload = true` to defer index load; throttle clone concurrency; schedule migrations outside import windows.

---

### Chain 3: Decommission → Clone Flood → Network Saturation → Cluster-wide Slowdown

```
Node decommission triggered; all tablets on that node need replicas elsewhere
  ↓ observable: SHOW PROC '/cluster_balance' shows pending decommission tasks; clone count begins rising
Scheduler creates clone tasks for all tablets on decommissioning node simultaneously
  ↓ observable: starrocks_be_clone_task_count spikes across multiple destination nodes
Concurrent clone tasks saturate inter-node network bandwidth
  ↓ observable: Network throughput near capacity; iostat shows high disk read on decommissioning node and write on destinations
All nodes experience increased network latency; read and write latency rises cluster-wide
  ↓ observable: Grafana p99 query latency rises across all nodes; BE log RPC timeout errors
```
Cluster-wide performance degradation during decommission.

**Trigger conditions**: Large node with many tablets decommissioned without throttling; replication factor at minimum; insufficient spare network capacity.

**Break point**: Limit clone tasks via `tablet_sched_max_scheduling_tablets`; decommission nodes one at a time; increase replication factor before decommission.

---

## Related Cases

- `case-004-disk-balancing` — IO saturation from migration retry loop

---

## Resources

- [Tablet Scheduler Documentation](https://docs.starrocks.io/docs/administration/management/tablet_management/)
- [fe_tablet_schedules System Table](https://docs.starrocks.io/docs/sql-reference/information_schema/fe_tablet_schedules/)
