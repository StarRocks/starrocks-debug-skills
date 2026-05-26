---
type: reference
category: shared-data
issue: datacache-corruption
keywords: [DataCache, StarCache, silent corruption, publish failure, compressed block]
---

# Case-010: DataCache Corruption Causing Publish Failures (Silent Data Corruption)

## Environment

- StarRocks version: 3.5.11
- Architecture: shared-data

## Symptom

Monitoring triggered alerts for abnormal publish failures with the error message
`corrupted compressed block contents`.

## Investigation

1. **Timeline**: alert at 01:08, RD involved at 01:22, suspected DataCache corruption at 01:58, resolved at 02:02.
2. **Log analysis**: error was reported the first time this transaction was published. Ruled
   out: tablet transfer, node restart, publish retry due to timeout.
3. **Code analysis**: identified a potential risk in StarCache (DataCache) that could lead
   to silent data corruption.
4. **Validation**: after clearing StarCache and forcing reads from S3, the issue disappeared.

## Root Cause

A bug in StarCache caused silent data corruption. Corrupted cached data was served during
publish, causing validation failures.

## Resolution

### Immediate

- Clear DataCache on affected machines:
  ```bash
  # Stop BE, remove DataCache directories on affected nodes, restart BE
  ```

### Long-term

- Fix all potential data corruption bugs in DataCache
  ([starcachelib#199](https://github.com/StarRocks/starcachelib/pull/199)).
- Implement a self-cleaning mechanism for corrupted cache entries caused by disk failures
  ([starrocks#69693](https://github.com/StarRocks/starrocks/pull/69693)).

## Lessons Learned

- "Corrupted compressed block" on a freshly published transaction is almost always cache
  corruption rather than ingest corruption — verify by clearing cache and rereading from S3.
- Self-healing for cache corruption belongs in the runtime; manual clearing should be a
  fallback, not the only recovery path.

## Related Skills

- [SKILL.md](../SKILL.md) — DataCache corruption diagnosis
