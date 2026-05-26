---
type: reference
category: shared-data
issue: stream-load-stuck
keywords: [Stream Load, tablet meta cache, S3 key not found, base version, shared-data]
---

# Case-009: Stream Load Stuck Due to Cached Tablet Meta Bug

## Environment

- StarRocks version: 4.0.2
- Architecture: shared-data

## Symptom

Stream Load tasks failed and remained stuck even after disabling ingestion for 30 minutes.
Error: `publish version fail because tablet meta file is missing` (`The specified key does not exist`).

## Investigation

1. **Identified the missing file**: meta file version was 32630, tablet_id was 37483777.
2. **Checked S3 directly**: the actual meta file on S3 had version 32628 — the version 32630
   file did not exist on S3.
3. **BE log analysis**: found log entry showing
   `base version adjusted from 32628 to 32630, because the latest tablet meta cache has meta which version is 32630`.
4. **Code analysis**: in 4.0.2, a new code path caches tablet metas that haven't been synced
   to S3 into the "latest tablet meta cache". When bundled tablet meta fails to sync to S3,
   the cache holds a version higher than what exists on S3. The `cal_new_base_version` logic
   assumes the latest cached version is already on S3, so it adjusts the base version
   accordingly. When reading from S3 at the adjusted version, the result is `key does not exist`.

## Root Cause

PR [#65661](https://github.com/StarRocks/starrocks/pull/65661) introduced a bug in 4.0.2 that
broke the assumption: "the latest cached tablet metadata must already exist in S3."
The meta files were not actually lost — the issue was an inconsistent cache state.

## Resolution

### Short-term

- Restart BE to reset the cache state.

### Fix

- PR [#66558](https://github.com/StarRocks/starrocks/pull/66558).

### Note

- This bug only exists in 4.0.2; 4.0.1 is not affected.

## Lessons Learned

- "File not found" in shared-data is rarely a real S3 deletion — first verify what S3
  actually has, then compare to the version BE expected.
- New caching code paths are a fertile source of consistency bugs; treat any cache that
  pre-sync state as suspicious until proven otherwise.

## Related Skills

- [SKILL.md](../SKILL.md) — cached tablet metadata inconsistencies
- [SKILL.md](../SKILL.md) — publish timeout investigation
