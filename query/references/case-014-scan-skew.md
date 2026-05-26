---
type: reference
category: query
issue: scan-skew
keywords: [scan skew, bucket key, aggregate model, compaction, MERGE, profile]
---

# Case-014: ScanNode Slow Due to Data Skew (Bucket Key Optimization)

## Symptom

Simple `GROUP BY` query on an aggregate table takes 12+ seconds despite scanning only ~1M rows.

## Investigation

1. Enabled profile (`SET is_report_success = true`) and checked `OLAP_SCAN_NODE`.
2. Found 15 tablets with `Active` time ranging from 34 ms to 5+ seconds — classic data skew pattern.
3. Checked `MERGE` phase: 10+ seconds on merge, indicating insufficient compaction
   (multiple rowsets per tablet).
4. Ran `SHOW TABLET FROM table` — `DataSize` varied wildly across tablets (some had hundreds
   of millions, some had hundreds of thousands).
5. Analyzed bucket key distribution: `(campaign_id, ad_id, creative_id, channel)` — found
   massive skew on `ad_id = -1`.

## Root Cause

Two compounding issues:

- **Insufficient compaction**: 8 rowsets per tablet instead of 1-2, causing slow `MERGE` on
  Aggregate table.
- **Poor bucket key selection**: `ad_id = -1` concentrated data in a few tablets.

## Resolution

1. Accelerated compaction:
   ```
   cumulative_compaction_check_interval_seconds=2
   cumulative_compaction_num_threads_per_disk=2
   base_compaction_num_threads_per_disk=2
   ```
2. Changed bucket key to include a high-cardinality column (`lastfrom`):
   ```sql
   DISTRIBUTED BY HASH(campaign_id, ad_id, creative_id, channel, lastfrom)
   ```
3. Query time dropped from 12s to 1-2s.

## Impact Chain

This data skew cascades as follows:

```
Low-cardinality bucket key → data concentrated in one/few tablets
  → Single scan instance 10x slower than peers (this case)
    → Query latency dominated by straggler scan node (query skill)
      → Adding BE nodes provides no relief — hot tablet stays on same node
    → Hot tablet BE: CPU/IO saturation from concentrated scans
      → Compaction: hot tablet accumulates rowsets faster → compaction lag (compaction skill)
        → MERGE time worsens further → query latency degrades progressively
      → Import: hot tablet BE becomes write bottleneck → async_delta_writer pool pressure (import skill)
```

**Cross-skill impact**: compaction (hot tablet compaction lag), import (write bottleneck on hot BE)

**Key diagnostic**: skew must be confirmed at tablet level (`SHOW TABLET FROM <table>` DataSize distribution) before blaming cluster resources — misleading Profile analysis may suggest "insufficient parallelism" when the real fix is re-bucketing.

## Lessons Learned

- The data-skew diagnostic pattern: Profile `Active` time variance + `SHOW TABLET DataSize`
  analysis. Either signal alone is enough to suspect skew; both together is conclusive.
- A "good" bucket key on paper can still produce skew if a sentinel value (like `-1`)
  dominates the data.

## Related Skills

- [SKILL.md](../SKILL.md) — scan performance analysis
- [SKILL.md](../SKILL.md) — bucket strategy best practices
