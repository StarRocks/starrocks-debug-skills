---
type: guide
category: cross-skill
keywords: [cascade, compaction, import, OOM, deadlock, cluster degradation, slow import, slow query]
---

# Cross-Skill Guide: Cluster Degradation Cascade

**Pattern**: Compaction backlog → Import slowdown → BE OOM → FE Deadlock

This guide covers the most common multi-skill cascade in production: a cluster that degrades slowly over hours until imports and queries both fail. Each symptom appears to be independent but they are causally linked.

---

## The Full Cascade

```
[Trigger] High-frequency small imports (or large PK table upserts)
  │
  ▼  (compaction skill)
Rowset accumulation: num_rowset per tablet > 100
  ↓ observable: SELECT num_rowset FROM information_schema.be_tablets ORDER BY num_rowset DESC LIMIT 10
  ↓ observable: Grafana compaction score rising; compaction bytes/sec < ingest rate
  │
  ▼  (compaction → query)
MERGE dominates OLAP_SCAN_NODE scan time → slow queries
  ↓ observable: Profile shows MERGE time dominant; p99 query latency rising
  │
  ▼  (compaction → import)
PK publish timeout: accumulated rowsets slow apply thread
  ↓ observable: finishTransaction log shows publish cost >> write cost
  ↓ observable: starrocks_be_publish_version_queue_count growing
  │
  ▼  (import → node)
Large queries to compensate for data staleness increase memory pressure
  ↓ observable: SHOW PROC '/current_queries' shows memUsageBytes climbing
  ↓ observable: grep "large memory alloc" be.WARNING shows many entries
  │
  ▼  (node)
BE OOM: Linux OOM Killer fires on one or more BEs
  ↓ observable: dmesg | grep -i oom; BE disappears from SHOW BACKENDS
  │
  ├─▶ (import) In-flight transactions lose publish context → cluster-wide publish timeout
  │     ↓ observable: all load jobs report "publish timeout" within minutes of BE death
  │
  ├─▶ (balance) FE triggers clone tasks to restore replica count
  │     ↓ observable: starrocks_be_clone_task_count spikes
  │     └─▶ Clone IO competes with remaining compaction → compaction lag worsens
  │
  └─▶ (node) FE deadlock risk: concurrent DDL during recovery + clone task lock contention
        ↓ observable: jstack shows BLOCKED threads; LockManager deadlock JSON in fe.log
        └─▶ Tablet reports freeze → version map stale → query "version does not exist"
```

---

## Diagnosis Sequence

Work **top-down** — identify the stage the cluster is currently in, then address the root.

### Stage 1: Is Compaction Behind?

```sql
-- Check rowset accumulation
SELECT table_name, tablet_id, num_rowset, num_segment, data_size
FROM information_schema.be_tablets t
JOIN information_schema.tables_config c ON t.table_id = c.table_id
ORDER BY num_rowset DESC LIMIT 10;
```

- `num_rowset > 100` → compaction behind → go to Stage 2
- `num_rowset < 20` → compaction is fine; skip to Stage 4

### Stage 2: Is Import or Query Already Impacted?

```bash
# Check publish queue depth
grep "finishTransaction" fe.log | grep "publish total cost" | tail -20
# Look for "publish total cost: Xs" values much larger than "write cost: Xs"

# Check import status
mysql -e "SELECT STATE, COUNT(*), AVG(TIMESTAMPDIFF(SECOND, LOAD_START_TIME, NOW())) avg_age
          FROM information_schema.loads
          WHERE STATE IN ('LOADING','PREPARED') GROUP BY STATE;"
```

- publish cost >> write cost → PK publish timeout chain is active
- Many LOADING jobs with high `avg_age` → import is already backed up

### Stage 3: Check Memory Pressure

```bash
# On each BE node — check for large memory allocations
grep "large memory alloc" /path/to/be.WARNING | tail -20

# Live query memory usage
mysql -e "SHOW PROC '/current_queries';" | grep -v "^+" | awk -F'|' '{print $2, $8}' | sort -k2 -nr | head -10
```

- Many `large memory alloc` entries → memory pressure building
- Top queries with large `memUsageBytes` → candidate for pre-emptive KILL

### Stage 4: Has BE OOM Occurred?

```bash
# Check all BE nodes
for node in be1 be2 be3; do ssh $node "dmesg | grep -i oom | tail -5"; done

# In FE
mysql -e "SHOW BACKENDS;" | grep -v "true.*true"  # Alive=false or DataUsed=0 after restart
```

- OOM confirmed → immediate action: check which load jobs are stuck, manually retry after BE recovers

### Stage 5: Is FE Deadlock Active?

```bash
# Check for deadlock (v3.3+)
grep "LockManager" /path/to/fe.log | tail -20

# Check for stale tablet report timestamp (deadlock symptom)
mysql -e "SHOW PROC '/statistic';" | grep "LastReport"
# If LastReport hasn't updated in >60s during active cluster → suspect deadlock

# Capture jstack for analysis
jstack $(cat fe/bin/fe.pid) > /tmp/fe_jstack_$(date +%s).log
grep -c "BLOCKED" /tmp/fe_jstack_*.log
```

---

## Intervention by Stage

| Stage | Immediate Action | Root Fix |
|---|---|---|
| Compaction behind | `ALTER TABLE <t> COMPACT;`; increase `cumulative_compaction_num_threads_per_disk` | Reduce import frequency; batch imports |
| Publish timeout | Set `skip_pk_preload = true`; increase `max_pk_compaction_threads` | Tune compaction to keep up with PK upsert rate |
| Memory pressure | `KILL QUERY <query_id>` for top memory consumers | Set `query_mem_limit`; enable query spill |
| BE OOM | Wait for BE auto-restart; manually retry failed load jobs | See above memory fixes |
| Clone flood after OOM | Throttle: `ADMIN SET CONFIG ("tablet_sched_max_scheduling_tablets" = "50")` | Set `skip_pk_preload = true` on clone destinations |
| FE Deadlock | `jstack` capture → FE rolling restart | Identify lock ordering from jstack; upgrade to version with fix |

---

## Prevention Checklist

- [ ] `num_rowset` alert: warn at 50, critical at 100 (check `information_schema.be_tablets`)
- [ ] `starrocks_be_publish_version_queue_count` alert: warn at 100
- [ ] BE memory alert: warn when RSS > 80% of `mem_limit`
- [ ] Import frequency control: batch small imports to > 1 min intervals per tablet
- [ ] `query_mem_limit` set to a safe fraction (e.g., 10–20% of BE RAM) for all query sessions
- [ ] Resource groups configured so ad-hoc analytics cannot monopolize cluster

---

## Related Skills and Cases

| Topic | Reference |
|---|---|
| Compaction lag | [compaction/SKILL.md](../compaction/SKILL.md) |
| PK publish timeout | [import/SKILL.md](../import/SKILL.md) — Section 7 |
| BE OOM localization | [node/SKILL.md](../node/SKILL.md) — Section 1 |
| FE deadlock | [node/SKILL.md](../node/SKILL.md) — Section 3 |
| Clone flood | [balance/SKILL.md](../balance/SKILL.md) — Chain 3 |
| BE OOM cascade case | [node/references/case-008-be-oom.md](../node/references/case-008-be-oom.md) |
| FE deadlock case | [node/references/case-003-fe-deadlock.md](../node/references/case-003-fe-deadlock.md) |
