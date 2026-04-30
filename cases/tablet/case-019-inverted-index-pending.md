---
type: case
category: tablet
issue: inverted-index
keywords: [inverted-index, builtin-index, pending, create-index, alter-table, history_job_keep_max_second]
---

# Case-019: Builtin Inverted Index Creation Pending After ALTER TABLE

## Environment

- StarRocks version: 4.1.0
- Architecture: shared-nothing (3 nodes, FE/BE co-deployed)
- Index type: builtin inverted index (GIN)
- Cluster state: No high-frequency imports, empty table with no writes

## Symptom

Customer reports inconsistent behavior when adding builtin inverted index:

**Works:**
- Adding inverted index during `CREATE TABLE` statement
- Adding inverted index after `CREATE TABLE` in test environment (4.1.0-rc, single node)

**Fails:**
- Adding inverted index via `CREATE INDEX` statement in production (4.1.0, 3 nodes)
- Index creation remains in `PENDING` state indefinitely

**Commands Used:**

```sql
-- Create index command
CREATE INDEX idx_cp_gin_customer_name
ON dws_contract_price_4_vocc_test (customer_name)
USING GIN ("parser" = "standard", "imp_lib" = "builtin");

-- Check index status
SHOW ALTER TABLE COLUMN FROM dw_rate_filing_bak 
WHERE TableName = 'dws_contract_price_4_vocc_test';
```

**Error:**

```
Index creation stuck in PENDING state.
FE log shows only 2 entries for the job_id, no further progress.
```

## Investigation

### Step 1: Compare Environments

| Attribute | Production | Test |
|---|---|---|
| Version | 4.1.0 | 4.1.0-rc |
| Nodes | 3 nodes | 1 node |
| FE/BE | Co-deployed | Single node |
| Result | PENDING | Works |

### Step 2: Check Cluster Load

User verified via monitoring:
- No high-frequency imports
- Empty table, no write operations
- No resource contention

### Step 3: Check FE Logs

FE logs showed only 2 entries for the job_id, indicating the job was not progressing.

**Initial hypothesis**: Some previous imports might be stuck.

## Root Cause

**Bug in 4.1.0**: The `history_job_keep_max_second` configuration causes schema change jobs to be cleaned up too quickly, preventing new index creation jobs from starting properly.

This is a known bug fixed in version 4.1.1.

## Resolution

### Short-term Workaround

Increase `history_job_keep_max_second` to prevent premature job cleanup:

```sql
ADMIN SET FRONTEND CONFIG ("history_job_keep_max_second" = "31536000");
```

This keeps history jobs for 1 year (31536000 seconds), allowing the index creation to proceed.

### Long-term Solution

Upgrade to version 4.1.1 or later where this bug is fixed.

**Fix PR**: https://github.com/StarRocks/starrocks/pull/70934

## Key Commands

```sql
-- Create builtin inverted index
CREATE INDEX idx_name ON table_name (column_name)
USING GIN ("parser" = "standard", "imp_lib" = "builtin");

-- Check index creation status
SHOW ALTER TABLE COLUMN FROM database_name 
WHERE TableName = 'table_name';

-- Workaround: Increase history job keep time
ADMIN SET FRONTEND CONFIG ("history_job_keep_max_second" = "31536000");

-- Check FE logs for job progress
-- (On FE node)
grep "job_id" fe.log | grep -i "index\|pending\|schema"
```

## Lessons Learned

1. **Version-specific bugs**: Always check if the issue is a known bug in the specific version
2. **Configuration impact**: `history_job_keep_max_second` can affect schema change job scheduling
3. **Environment differences**: Multi-node vs single-node may expose different behaviors due to job scheduling differences
4. **Empty table assumption**: Even with empty tables and no writes, internal job scheduling can block operations

---

## Related Issues

- PR #70934 - Fix for history job scheduling bug in 4.1.1
- Similar issues may occur with other schema change operations (ALTER TABLE, ADD COLUMN, etc.)

## References

- GitHub PR: https://github.com/StarRocks/starrocks/pull/70934