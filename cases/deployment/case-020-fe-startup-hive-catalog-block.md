---
type: case
category: deployment
issue: fe-startup
keywords: [fe-startup, hive-catalog, lazy-connector, port-blocked, catalog-connection]
---

# Case-020: FE Startup Blocked by Hive Catalog Connection Failure

## Environment

- StarRocks version: 3.2.11
- Architecture: shared-nothing
- Component: FE startup
- External catalog: Hive catalog

## Symptom

FE startup process blocked when attempting to connect to Hive catalog:

- FE cannot complete startup
- Port 9030 remains unavailable
- FE keeps retrying catalog connection indefinitely
- No other operations can proceed

**Error:**

```
FE startup stuck, unable to connect to Hive catalog.
9030 port not available.
Connection retries blocking main thread.
```

## Investigation

### Step 1: Check Main Thread Status

User was asked to run `jstack` to check where main thread is stuck:

```bash
jstack <fe_pid> | grep -A 20 "main"
```

[PENDING: Need jstack output showing main thread state]

### Step 2: Analyze Stack Trace

The stack trace showed that `loadImage` process was blocking the main thread during catalog initialization.

### Step 3: Root Cause Analysis

In version 3.2.11, FE attempts to connect to external catalogs during startup, and if connection fails, it blocks the main thread indefinitely. This prevents FE from completing startup.

## Root Cause

**Design issue in 3.2.x**: FE synchronously initializes external catalogs during startup, blocking the main thread if catalog connection fails.

The `loadImage` process for catalog metadata runs on the main thread, causing the entire FE startup to be blocked when:
1. Catalog connection is unreachable
2. Network issues prevent catalog access
3. Catalog service is down

## Resolution

### Short-term Workaround

[PENDING: Need workaround - possibly remove catalog configuration temporarily, start FE, then add catalog]

Options:
1. Temporarily remove Hive catalog configuration from `fe.conf`
2. Start FE without catalog, then add catalog after FE is running
3. Ensure Hive metastore is reachable before FE startup

### Long-term Solution

Upgrade to version 3.5.0 or later where **lazy connector** is implemented.

**Fix PR**: https://github.com/StarRocks/starrocks/pull/47402

This PR changes catalog initialization to async mode:
- Catalog connections are initialized lazily
- loadImage runs asynchronously, not blocking main thread
- FE can complete startup even if catalog is unreachable

**Note**: According to discussion, version 3.3 was considered but not sufficient; version 3.5.0 is the recommended upgrade target.

## Key Commands

```bash
# Check FE thread state
jstack <fe_pid> | grep -A 30 "main"

# Check FE log for catalog connection errors
grep -i "catalog\|hive\|metastore\|connection" fe.log | grep -i "error\|fail\|timeout"

# Check if port 9030 is available
netstat -tlnp | grep 9030

# Workaround: Remove catalog from fe.conf temporarily
# (Edit fe.conf to remove hive catalog configuration)
```

```sql
-- After FE starts successfully, add catalog
CREATE EXTERNAL CATALOG hive_catalog PROPERTIES (
    "type" = "hive",
    "hive.metastore.uris" = "thrift://<metastore_host>:<port>"
);
```

## Lessons Learned

1. **Catalog initialization blocking**: In older versions, external catalog initialization blocks FE startup
2. **Lazy connector improvement**: Newer versions use async/lazy initialization to prevent blocking
3. **Upgrade path**: This issue requires upgrade to 3.5.0, not just 3.3.x
4. **Diagnosis method**: jstack is critical for diagnosing FE startup hangs

---

## Related Issues

- PR #47402 - Lazy connector implementation
- Similar blocking issues may occur with other external catalogs (Iceberg, Hudi, etc.)

## References

- GitHub PR: https://github.com/StarRocks/starrocks/pull/47402