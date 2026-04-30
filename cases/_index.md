---
type: index
category: cases
description: Real-world troubleshooting case studies organized by category
---

# Cases Index

This directory contains real-world troubleshooting cases with complete investigation walkthroughs.

## Case Categories

| Category | Description | Directory |
|---|---|---|
| query | Query hang, slow query, profile analysis | [query/](query/) |
| import | Import slow, timeout, RPC failed, publish timeout | [import/](import/) |
| node | BE Crash, BE OOM, FE deadlock, FE Full GC | [node/](node/) |
| materialized-view | MV refresh failures, rewrite failures | [materialized-view/](materialized-view/) |
| data-lake | HMS, Kerberos, external table errors | [data-lake/](data-lake/) |
| shared-data | DataCache, S3 issues in shared-data | [shared-data/](shared-data/) |
| tablet | Tablet health, balance, data skew | [tablet/](tablet/) |
| deployment | FE/BE startup failures, port conflicts | [deployment/](deployment/) |
| concurrency | High QPS, memory volatility | [concurrency/](concurrency/) |

## Quick Reference

| Case ID | Category | Issue Type | Summary |
|---|---|---|---|
| case-001 | import | Broker Load backlog | Task queue saturation |
| case-002 | import | RPC Failed | Statistics collection starving RPC |
| case-003 | node | FE Deadlock | LockManager deadlock |
| case-004 | tablet | Disk balancing | Migration retry loop |
| case-005 | deployment | SSL Certificate | Private OSS SSL issue |
| case-006 | data-lake | Network saturation | HMS blocking |
| case-007 | node | Memory tracking leak | System memory exhausted |
| case-008 | node | BE OOM | OOM causing publish stall |
| case-009 | shared-data | Stream Load stuck | Tablet meta cache bug |
| case-010 | shared-data | DataCache corruption | Silent data corruption |
| case-011 | shared-data | Multi-root-cause | Leader switch + S3 failures |
| case-012 | materialized-view | Refresh failures | FE abort + S3 rate limit |
| case-013 | shared-data | DataCache autoscaling | Cache eviction regression |
| case-014 | query | Scan skew | Bucket key optimization |
| case-015 | concurrency | Memory volatility | Session timeout override |
| case-016 | data-lake | Kerberos auth | Kerberos authentication failures |
| case-017 | import | Reached timeout | Replica sync thread pool saturation |
| case-018 | import | ORC compression overflow | Large ORC file import fails, use zstd or reduce file size |
| case-019 | tablet | Inverted index pending | ALTER TABLE ADD INDEX stuck in PENDING, workaround with history_job_keep_max_second |
| case-020 | deployment | FE startup blocked | Hive catalog connection blocks FE startup, upgrade to 3.5.0 with lazy connector |
| case-021 | node | FE OOM metadata | Frequent table create/drop causes memory pressure, use DROP FORCE and reduce concurrency |
| case-022 | node | FE OOM complex SQL | Complex SQL plan causes memory spike, split queries and scale FE memory |
| case-023 | node | FE OOM MV refresh | Frequent MV refresh causes slow lock, adjust refresh interval to hourly/daily |
| case-024 | node | FE OOM Iceberg | Iceberg query with delete files causes memory spike, maintain Iceberg tables regularly |
| case-025 | node | FE OOM Insert leak | InsertLoadJob memory leak in 3.1-3.3 versions, upgrade to fixed version |
| case-026 | node | FE crash async-profiler | async-profiler causes JVM crash, disable proc_profile_cpu_enable |
| case-027 | node | FE OOM heap config | Insufficient JVM heap causes OOM, minimum 16GB for production |
| case-029 | node | FE memory slow growth | Replica memory leak in 3.3.13-3.3.17, upgrade to 3.3.18+ |
| case-030 | node | FE machine memory | glibc thread arena causes off-heap growth, set MALLOC_ARENA_MAX=1 |
| case-031 | node | FE memory spike traffic | Arrow Flight proxy causes traffic surge, disable arrow_flight_proxy_enabled |

## Case Template

Create file: `cases/<category>/case-<number>-<short-name>.md`

```markdown
---
type: case
category: <category>
issue: <issue-type>
keywords: [keyword1, keyword2, ...]
---

# Case-XXX: <Short Title>

## Environment

- StarRocks version: X.X.X
- Architecture: shared-data / shared-nothing

## Symptom

<What user reported>

**Error:**
```
<Exact error>
```

## Investigation

### Step 1: <Action>

<Command and analysis>

### Step 2: ...

## Root Cause

<Underlying issue>

## Resolution

### Short-term

<Immediate fix>

### Long-term

<Permanent solution>

## Lessons Learned

<What we learned>

---

## Related Skills

- [XX-<skill>.md](../../skills/XX-<skill>.md)
```

## Adding New Case

1. Choose category and sequential number
2. Create file with YAML frontmatter
3. Follow template structure
4. Update this quick reference table