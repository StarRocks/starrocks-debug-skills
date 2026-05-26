---
type: reference
category: tablet
issue: disk-balancing
keywords: [disk balance, _shutdown_tablets, IO saturation, migration retry]
---

# Case-004: Disk Balancing Failures Saturating IO (Migration Retry Loop)

## Environment

- StarRocks version: 3.1.17
- Architecture: shared-nothing

## Symptom

Continuous disk migration tasks firing, `ioutil` at max, impacting queries and writes.

## Investigation

1. **Analyzed migration tasks**: tasks kept failing and retrying.
2. **Failure cause**: target disk already contained metadata for the same tablet.
3. **Code analysis**:
   - Migration start attempts to delete `TABLET_SHUTDOWN` tablets on the target disk.
   - If not found in `_shutdown_tablets`, deletion is considered successful.
   - However, when actual metadata deletion failed, the `tablet_id` was still removed from `_shutdown_tablets`.
   - Subsequent retries found metadata still present, causing continuous failures.

## Root Cause

Failed metadata deletion incorrectly removed the `tablet_id` from `_shutdown_tablets`,
preventing proper cleanup on retry. Migration tasks retried endlessly, saturating IO.

## Resolution

### Short-term

- Restart all BEs — `_shutdown_tablets` state resets consistently, migration completes.

### Long-term

- Fix code to not remove `tablet_id` from `_shutdown_tablets` on metadata deletion failure.
- Optimize migration retry strategy to avoid IO saturation.

## Lessons Learned

- An unbounded retry loop with high IO is itself the primary failure — even if the proximate
  cause is benign, the cluster cannot recover until the loop is broken.
- BE-wide restart is a heavy hammer but resets ephemeral migration bookkeeping cleanly.

## Related Skills

- [SKILL.md](../SKILL.md) — disk-balancing failure pattern
