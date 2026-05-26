---
type: reference
category: shared-data
issue: multi-root-cause
keywords: [FE leader switch, GTID, S3 multipart upload, dat file loss, sst file loss, scaling, vacuum]
---

# Case-011: Multi-Root-Cause Incident After Cluster Scaling

## Environment

- StarRocks version: 3.5.11
- Architecture: shared-data, scaled from 72 to 96 nodes

## Symptom

After scaling, three types of errors appeared simultaneously:

1. Meta file not found.
2. Data file (`.dat`) not found.
3. SST file not found.

## Investigation — 10-Phase Incident Timeline

1. **Emergency**: ingestion and query failures reported. Prepared emergency patch disabling GTID validation.
2. **Partial recovery**: most tables resumed; one table had transactions stuck for ~10 hours before auto-committing.
3. **Secondary failures**: SST file corruption surfaced on some tablets. Recovered via ALTER SQL. Then `.dat` file not found errors appeared — indicating a second independent issue.
4. **Root cause hypothesis**: discovered FE leader switched seconds before "meta file not found" error. Strong temporal correlation.
5. **Multi-layer recovery**: meta issue confirmed as leader-switch bug. Dat issue: missing dat blocked publish, attempted manual compaction plus table reconstruction.
6. **Stabilization**: disabled FE graceful exit; deployed monitoring; increased `lake_metadata_cache_limit` to 8G; set `lake_autovacuum_grace_period_minutes = 4320`.
7. **Recurrence**: dat file not found reoccurred post-upgrade, before vacuum modification — additional unknown trigger.
8. **Deep narrowing**: suspected S3 write failure. BE restart may cause data loss. Data loss only occurred when GTID was disabled.
9. **Final root cause**: after obtaining S3 permission to list multipart uploads, confirmed S3 multipart upload failures causing dat/sst file loss.
10. **Final recovery**: new patch applied, rolling restart completed. Cluster stable.

## Root Cause A — Meta file not found

- The meta file was NOT actually missing from S3.
- GTID validation inside the meta file failed, so the system treated it as obsolete and returned `NOT_FOUND`.
- During FE graceful exit, EditLog write failed but FE continued sending publish tasks. After leader switch, the new leader couldn't replay the missing transaction, leading to `txn_version` collision, meta file name collision, and GTID validation failure.
- Fix: disable FE graceful exit; fix the EditLog persistence bug.

## Root Cause B — Data/SST file not found

- S3 multipart upload failures caused incomplete file writes.
- Missing `.dat` and `.sst` files blocked publish operations.
- Fix: add dat/sst loss metrics, improve S3 upload reliability, add recovery fallback.

## Key Parameters Applied

```
-- Increase metadata cache
lake_metadata_cache_limit = 8G

-- Conservative vacuum (3 days grace period)
lake_autovacuum_grace_period_minutes = 4320

-- Disable FE graceful exit
-- (prevents leader switch EditLog persistence bugs)
```

## Lessons Learned

- A complex incident may have multiple **independent** root causes. Resist the urge to
  explain everything with one theory — investigate each error type separately.
- For shared-data, S3 visibility is critical. Listing in-progress multipart uploads is
  often the only way to confirm upload-side data loss.
- "Disable graceful exit" is a heavy-handed mitigation but acceptable when the alternative
  is leader-switch metadata corruption.

## Related Skills

- [SKILL.md](../SKILL.md) — leader switch issues; S3 multipart upload failures; vacuum tuning
