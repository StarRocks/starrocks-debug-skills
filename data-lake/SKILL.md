---
name: data-lake
description: "Use when external catalog queries (Hive, Iceberg, S3, HDFS, Paimon) are failing, hanging, or returning stale data. Covers Hive Metastore connectivity errors, Kerberos authentication failures (keytab expiry, GSS errors, clock skew), S3 rate limiting and SSL issues, network saturation from HMS, and metadata cache TTL tuning for data freshness."
version: 2.0.0
category: data-lake
keywords:
- HMS
- Hive
- Kerberos
- HDFS
- S3
- external table
- data lake
- catalog
- cache
- metadata cache
- data cache
- freshness
- Iceberg
tools:
- jstack
related_cases:
- case-005-ssl-certificate
- case-006-network-saturation
- case-016-kerberos-authentication
---

# Data Lake / External Table Troubleshooting

Investigation guide for Hive Metastore connectivity, Kerberos authentication, HDFS/S3
access errors, and external catalog query failures.

Five root causes account for the vast majority of cases:

- **Cause A** — HMS connectivity failure or metadata listing too slow
- **Cause B** — Kerberos authentication failure (expired keytab or clock skew)
- **Cause C** — S3 / object storage access or rate-limit error
- **Cause D** — Network saturation from HMS traffic on CN/BE nodes
- **Cause E** — Metadata cache staleness (Hive partitions, Iceberg snapshots)

---

## Metric Taxonomy — Read This First

Before using any diagnostic commands, understand the observable signals for external table issues.

### FE log signals (`fe.log`)

| Signal | Meaning |
|---|---|
| `Start to connect to Hive metastore` + no subsequent `Connected` | HMS TCP connection blocking or timing out |
| `getPartitions` / `listPartitions` with high elapsed ms | HMS partition listing slow (large partition count or HMS overload) |
| `Finished replaying journal` never appears after HMS connect attempt | HMS blocking FE startup sequence |
| `AuthenticationException` from catalog connector | Kerberos ticket invalid or HMS auth rejected |

### BE log signals (`be.log` / `be.WARNING`)

| Signal | Meaning |
|---|---|
| `GSS initiate failed` | Kerberos GSSAPI handshake failed — expired keytab or JCE missing |
| `javax.security.sasl.SaslException` | SASL negotiation failed — auth or encryption mismatch |
| `503 reduce request rate` | S3 rate limit hit — partition prefix count too low |
| `Filesystem closed` | HDFS NameNode cache too small — `hdfs_client_max_cache_size` must exceed NN count |
| Network I/O bytes/sec approaching NIC capacity | External scan saturating link |

### Network / thread signals

| Signal | Meaning |
|---|---|
| jstack shows threads blocked on `TFilterTransport.readAll` | HMS RPC call blocking; HMS slow or overloaded |
| `sar -n DEV 1 5` shows RX/TX near line rate on CN/BE host | Network saturation from remote scan |
| `netstat -s \| grep retransmit` spike during external scan | Packet loss from bandwidth burst |

### How to retrieve signals

**Option 1 — FE log grep**

```bash
# Check HMS connectivity on FE startup
grep -E "connect.*Hive metastore|Connected to HMS|AuthenticationException" fe.log | tail -30

# Check partition listing latency warnings
grep -E "getPartitions|listPartitions" fe.log | tail -20

# Check if FE completed startup
grep "Finished replaying journal" fe.log | tail -5
```

**Option 2 — BE log grep**

```bash
# GSS / Kerberos errors on BE
grep -E "GSS initiate failed|SaslException|kinit" be.log | tail -20

# S3 rate limit errors
grep -E "503|reduce request rate|rate limit" be.log | tail -20

# HDFS filesystem errors
grep -E "Filesystem closed|hdfs_client" be.log | tail -20
```

**Option 3 — Kerberos ticket status**

```bash
# Check ticket cache expiry
klist

# List principals in keytab file
klist -kt /path/to/keytab

# Test kinit manually
kinit -kt /path/to/keytab principal_name@REALM
```

**Option 4 — Network thread analysis**

```bash
# jstack to detect HMS-blocked threads (FE)
jstack $(cat fe/bin/fe.pid) > /tmp/fe_jstack.log
grep -A 5 "TFilterTransport\|HiveMetaStore" /tmp/fe_jstack.log | head -40

# Network I/O rate on BE host
sar -n DEV 1 5

# S3 / HDFS endpoint connectivity
curl -v --connect-timeout 10 http://<s3-endpoint>/<bucket>/<key>
telnet <hms-host> <hms-port>
```

---

**Key rules for interpretation**:

1. If HMS connection attempt has no response for >60s in `fe.log`, suspect TCP-level block (firewall, wrong port, HMS down) rather than auth.
2. `GSS initiate failed` always means Kerberos — check `klist` first; if ticket is valid, check JCE installation.
3. S3 rate-limit errors (`503`) escalate with partition count — the fix is `num_partitioned_prefix`, not retry tuning.
4. `Filesystem closed` with `hdfs_client_max_cache_size` too small causes random HMS disconnects that look like auth failures.

---

## External Table Issue Reference

| Error keyword / symptom | Root cause | Quick confirm |
|---|---|---|
| `GSS initiate failed` | Kerberos — expired keytab or missing JCE | `klist` on BE host; `klist -kt <keytab>` |
| `Server not found in Kerberos database` | SPN mismatch | Verify service principal in `krb5.conf` matches HMS |
| `Clock skew too great` | NTP not synced | `timedatectl` / `ntpstat` on FE and BE hosts |
| `Preauthentication failed` | Wrong keytab/password | Regenerate keytab from KDC |
| `Filesystem closed` | NN cache too small | Check `hdfs_client_max_cache_size` < NN count |
| `Failed to get current notification event id` | HMS user missing permissions | Check HMS user; set `HADOOP_USER_NAME` |
| `set_ugi() not successful` | Missing `HADOOP_USER_NAME` in hadoop_env.sh | Set env var; check Kerberos config |
| `503 reduce request rate` from S3 | S3 partition prefix rate limit | Increase `num_partitioned_prefix` |
| SSL handshake error to private OSS | Default SSL on; param bug in older versions | Upgrade; set `ssl_enable = false` in catalog |
| jstack: threads on `TFilterTransport.readAll` | HMS overloaded or slow | Check HMS thread pool; add HMS timeout config |
| New Hive partitions not visible | Metadata cache TTL | `REFRESH DATABASE` or reduce `metastore_cache_ttl_sec` |
| Iceberg sees old snapshot | Snapshot cache TTL | Reduce `iceberg_table_cache_ttl_sec`; `REFRESH TABLE` |

---

## Phase 1 — Classify the Issue

### Step 1.1 — Identify failure category

Check in order:

```bash
# 1. Is HMS reachable?
telnet <hms-host> <hms-port>
# Success → go to Step 1.2 (auth / metadata)
# Refused / timeout → Cause A (HMS connectivity)

# 2. Is auth failing?
grep -E "GSS initiate|SaslException|AuthenticationException" be.log fe.log | tail -10
# Found → Cause B (Kerberos)

# 3. Is S3 rate-limiting?
grep -E "503|reduce request rate" be.log | tail -10
# Found → Cause C (S3)

# 4. Is network saturating?
sar -n DEV 1 5
# RX/TX near line rate → Cause D (network)

# 5. Is data stale?
# If queries succeed but return old/missing data → Cause E (cache freshness)
```

**Symptom → root cause signal**:

| Pattern | Points toward |
|---|---|
| HMS TCP connect fails or hangs >60s | **Cause A** — HMS connectivity |
| `GSS initiate failed` or `Clock skew` in logs | **Cause B** — Kerberos auth |
| `503` or `rate limit` from S3 in BE log | **Cause C** — S3 rate limit |
| jstack threads blocked on `TFilterTransport`; NIC near line rate | **Cause D** — network saturation |
| Queries succeed but data is stale or partitions missing | **Cause E** — cache freshness |

---

## Phase 2 — Locate and Characterize

### Step 2.1 — Confirm HMS connectivity

```bash
# Test TCP reachability
telnet <hms-host> 9083

# Check FE log for HMS connection timeline
grep -E "connect.*metastore|Finished replaying" fe.log | tail -20

# Check if catalog is blocking FE startup
grep "Finished replaying journal" fe.log
# If this line never appears after catalog connect attempt → Cause A (Startup block)
```

### Step 2.2 — Confirm Kerberos state

```bash
# Is the ticket valid?
klist
# Look for: Expires < now → renew immediately

# Is the keytab readable?
klist -kt /path/to/keytab

# Enable debug output (temporary)
# In fe.conf JAVA_OPTS:
# -Dsun.security.krb5.debug=true
# In be/conf/hadoop_env.sh:
# HADOOP_OPTS="$HADOOP_OPTS -Dsun.security.krb5.debug=true"
# Debug output goes to fe.out / be.out
```

### Step 2.3 — Confirm S3 access

```bash
# Check S3 endpoint connectivity and rate errors
grep -E "503|rate limit|connection.maximum" be.log | tail -20

# Check catalog config for connection pool
SHOW CREATE CATALOG <catalog_name>;
# Look for: fs.s3a.connection.maximum setting
```

### Step 2.4 — Characterize cache staleness

```sql
-- Check current catalog cache config
SHOW CREATE CATALOG <catalog_name>;

-- Check if manual refresh fixes the issue
REFRESH TABLE <catalog_name>.<db_name>.<table_name>;
-- If query now returns fresh data → Cause E confirmed
```

---

## Phase 3 — Take Action by Cause

### Cause A — HMS Connectivity / Metadata Slow

**Confirm all match**:
- `telnet <hms-host> <port>` fails or hangs >30s
- `fe.log` shows HMS connect attempt with no completion
- OR `listPartitions` calls show high latency (>10s)

**Mechanism**: FE catalog module connects to HMS via Thrift RPC at startup and at query planning time. If HMS is unreachable or its thread pool is saturated, FE blocks waiting for partition metadata. For large catalogs (>10k partitions), each planning call lists partitions serially — HMS response time directly extends query planning time.

**Actions**:

```bash
# Step A-1: Verify HMS is reachable
telnet <hms-host> 9083
nc -zv <hms-host> 9083

# Step A-2: Check HMS service health independently
# (SSH to HMS host and check HMS logs / process status)

# Step A-3: Temporarily remove catalog to allow FE to start
# (edit fe.conf, remove catalog section, restart FE)
```

```sql
-- Step A-4: Enable metadata caching to reduce HMS call frequency
ALTER CATALOG <catalog_name> SET PROPERTIES (
    "hive.metastore.cache.ttl" = "600",
    "metastore_cache_refresh_interval_sec" = "120"
);

-- Step A-5: Add HMS connection timeout (prevents indefinite block)
ALTER CATALOG <catalog_name> SET PROPERTIES (
    "hive.metastore.timeout" = "10"
);
```

```bash
# Step A-6: If HMS slow due to large partition count — apply partition pruning
# Ensure queries use partition-key predicates in WHERE clause
# Check EXPLAIN output for PartitionPruning: true
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause B — Kerberos Authentication Failure

**Confirm all match**:
- `klist` shows expired ticket OR `klist -kt` shows keytab principal mismatch
- `be.log` shows `GSS initiate failed` or `Preauthentication failed`
- OR `Clock skew too great` in logs

**Mechanism**: Kerberos uses time-limited tickets (TGTs). When the TGT expires and automatic renewal is not configured, every HMS/HDFS RPC from BE fails at the GSSAPI layer. Clock skew >5 minutes also invalidates otherwise-valid tickets because Kerberos has a built-in replay-attack window. JCE (Java Cryptography Extensions) is required for AES-256 encryption used by modern KDCs.

**Actions**:

```bash
# Step B-1: Renew ticket immediately
kinit -kt /path/to/keytab principal_name@REALM
klist  # Confirm new expiry time

# Step B-2: Fix clock skew if present
timedatectl status
# If not synced: systemctl restart chronyd (or ntpd)

# Step B-3: Install JCE if AES-256 is used
# Download from Oracle: Java Cryptography Extension (JCE) Unlimited Strength
# Place in $JAVA_HOME/jre/lib/security/

# Step B-4: Set up automatic ticket renewal (permanent fix)
# Add to crontab on each FE/BE host:
# */30 * * * * kinit -R -kt /path/to/keytab principal@REALM 2>/dev/null || kinit -kt /path/to/keytab principal@REALM

# Step B-5: Verify service principal matches HMS
# krb5.conf hive.service.kerberos.principal must match HMS server config
```

| Error | Cause | Fix |
|---|---|---|
| `GSS initiate failed` | Expired keytab or missing JCE | Renew keytab; install JCE for AES-256 |
| `Server not found in Kerberos database` | SPN mismatch | Verify service principal matches server |
| `Clock skew too great` | NTP not synced | Ensure NTP/chrony is running |
| `Preauthentication failed` | Wrong keytab/password | Regenerate keytab from KDC |
| `Checksum failed` | Encryption type mismatch | Check `krb5.conf` encryption settings |

→ **Go to Phase 4: Verify Recovery**

---

### Cause C — S3 / Object Storage Issues

**Confirm all match**:
- `be.log` shows `503 reduce request rate` from S3 endpoint
- OR S3 connection pool exhaustion errors
- OR SSL handshake failure to private OSS

**Mechanism**: AWS S3 and S3-compatible storage rate-limit requests per prefix. When all partitions share the same prefix path, a high-concurrency scan sends many requests to a single prefix, triggering the 503 rate limit. Connection pool exhaustion occurs when concurrent scans exceed `fs.s3a.connection.maximum`, causing new requests to queue indefinitely.

**Actions**:

```bash
# Step C-1: Check current S3 error rate
grep -c "503\|rate limit" be.log

# Step C-2: Check connection pool size in catalog config
SHOW CREATE CATALOG <catalog_name>;
# Look for: fs.s3a.connection.maximum (default is often 32)
```

```sql
-- Step C-3: Increase S3 connection pool size
ALTER CATALOG <catalog_name> SET PROPERTIES (
    "fs.s3a.connection.maximum" = "200"
    -- For Paimon: "paimon.option.fs.s3a.connection.maximum" = "200"
);

-- Step C-4: Add partition prefixes to distribute S3 rate limits
-- This requires table-level prefix configuration during table creation
-- See: num_partitioned_prefix in Hive catalog docs

-- Step C-5: For IAM Role on EC2 (no AK/SK needed)
-- Ensure AWS_EC2_METADATA_DISABLED is NOT set to true
-- Catalog properties: use instance profile auth, omit aws.s3.access_key
```

```bash
# Step C-6: For SSL issues with private OSS
# Check: does the OSS endpoint support SSL?
openssl s_client -connect <oss-endpoint>:443 -brief

# If SSL not supported — disable in catalog:
# ALTER CATALOG ... SET PROPERTIES ("ssl_enable" = "false");
# Note: requires a version where this parameter takes effect
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause D — Network Saturation from HMS / Remote Scan

**Confirm all match**:
- `sar -n DEV 1 5` shows TX/RX near NIC line rate on CN/BE host
- jstack shows threads blocked on `org.apache.hadoop.hive.metastore.security.TFilterTransport.readAll`
- External table scans are concurrent and involve large data volumes or large JOINs

**Mechanism**: When BEs perform large external table scans (HDFS/S3), they read remote blocks at full network speed. Multiple concurrent scans share the same NIC uplink. If a JOIN between an internal and external table generates a large shuffle, both the remote read and the shuffle compete for the same network link, causing saturation. HMS RPC calls also use the same network path.

**Actions**:

```bash
# Step D-1: Confirm network saturation
sar -n DEV 1 10
# Look for: rxkB/s or txkB/s near theoretical line rate (e.g., 1Gbps ≈ 125MB/s = 125000 kB/s)

# Step D-2: Identify which external scan is responsible
```

```sql
-- Step D-3: Find active external scans
SHOW PROC '/current_queries';
-- Look for: queries with external table in FROM clause and high elapsed time

-- Step D-4: Reduce JOIN cardinality with predicate pushdown
-- Ensure WHERE predicate on partition key is present
EXPLAIN <external_table_query>;
-- Check: PartitionPruning: true in EXPLAIN output
```

```bash
# Step D-5: Add HMS timeout to prevent unbounded blocking
# In catalog config: hive.metastore.timeout = 10 (seconds)

# Step D-6: Limit external scan parallelism
# Per-query: SET parallel_scan_max_threads = 4;
# Or via resource group: set concurrency_limit on the group
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause E — Metadata Cache Freshness

**Confirm all match**:
- External table queries succeed but return stale or incomplete data
- New Hive partitions are not visible, or Iceberg query sees old snapshot
- `REFRESH TABLE` immediately fixes the issue

**Mechanism**: StarRocks FE caches Hive metastore metadata (partition lists, file locations) to avoid re-querying HMS for every query. Cache TTL controls staleness. For Hive, cache expires after `metastore_cache_ttl_sec` and is refreshed by a background daemon every 10 minutes. For Iceberg, cache is invalidated by `snapshot_id` change detection — but if the snapshot cache itself is stale, new snapshots are not detected promptly.

**Actions**:

```sql
-- Step E-1: Manual refresh (immediate fix)
REFRESH CATALOG <catalog_name>;
REFRESH DATABASE <catalog_name>.<db_name>;
REFRESH TABLE <catalog_name>.<db_name>.<table_name>;

-- Step E-2: Check current cache TTL settings
SHOW CREATE CATALOG <catalog_name>;

-- Step E-3: Reduce TTL for Hive catalogs with frequent updates
ALTER CATALOG <catalog_name> SET PROPERTIES (
    "metastore_cache_ttl_sec" = "60",           -- default varies
    "metastore_cache_refresh_interval_sec" = "60",
    "remote_file_cache_ttl_sec" = "60",
    "remote_file_cache_refresh_interval_sec" = "60"
);

-- Step E-4: Reduce TTL for Iceberg catalogs
ALTER CATALOG <catalog_name> SET PROPERTIES (
    "iceberg_table_cache_ttl_sec" = "300"       -- default 1800s (30 min)
);
```

**Background refresh config (fe.conf)**:

| Property | Default | Description |
|---|---|---|
| `background_refresh_metadata_interval_millis` | 600000 (10 min) | Interval between refresh runs |
| `background_refresh_metadata_time_secs_since_last_access_secs` | 86400 (24 hours) | Refresh tables accessed within this window |

**Cache freshness guarantees by catalog type**:

| Catalog Type | Freshness Behavior |
|---|---|
| Hive | Background refresh every 10 min; stale data served during refresh if TTL expired |
| Iceberg | Snapshot-based invalidation; background refresh keeps snapshot info current |

| Issue | Fix |
|---|---|
| Hive data stale | Reduce `metastore_cache_ttl_sec` and `remote_file_cache_ttl_sec` |
| Hive refresh too slow | Reduce `metastore_cache_refresh_interval_sec` |
| New partitions not visible | Call `REFRESH DATABASE` or `REFRESH TABLE` manually |
| Iceberg snapshot lag | Check `iceberg_table_cache_ttl_sec`; call `REFRESH TABLE` |

→ **Go to Phase 4: Verify Recovery**

---

## Phase 4 — Verify Recovery

```bash
# Verify HMS connectivity
telnet <hms-host> <hms-port>

# Verify Kerberos ticket valid
klist
# Confirm: Expires > now + 1h

# Verify no auth errors in BE log
grep -E "GSS initiate|SaslException" be.log | tail -5
# Should be empty or all old timestamps

# Verify S3 errors cleared
grep -E "503|rate limit" be.log | tail -5
# Should be empty or old timestamps
```

```sql
-- Verify external table is queryable
SELECT COUNT(*) FROM <catalog_name>.<db_name>.<table_name>;

-- Verify new partitions visible (if Cause E)
SHOW PARTITIONS FROM <catalog_name>.<db_name>.<table_name>;

-- Verify query completes within expected time
-- Compare: query time before vs after fix
```

```bash
# Verify network saturation resolved
sar -n DEV 1 5
# RX/TX should be well below line rate during normal query load
```

---

## External Catalog Config Parameters

| Parameter | Default | Description | Dynamic |
|---|---|---|---|
| `hdfs_client_max_cache_size` | 48 | Max cached HDFS NameNode clients; must exceed NN count | No (restart) |
| `metastore_cache_ttl_sec` | varies | TTL for Hive metastore metadata | Yes (ALTER CATALOG) |
| `metastore_cache_refresh_interval_sec` | varies | Refresh interval for metastore cache | Yes (ALTER CATALOG) |
| `remote_file_cache_ttl_sec` | varies | TTL for remote file metadata | Yes (ALTER CATALOG) |
| `remote_file_cache_refresh_interval_sec` | varies | Refresh interval for file cache | Yes (ALTER CATALOG) |
| `iceberg_table_cache_ttl_sec` | 1800 | TTL for Iceberg table metadata | Yes (ALTER CATALOG) |
| `iceberg_meta_cache_ttl_sec` | 172800 (48h) | TTL for Iceberg DB/partition metadata | Yes (ALTER CATALOG) |
| `fs.s3a.connection.maximum` | 32 | S3 connection pool size per catalog | Yes (ALTER CATALOG) |
| `hive.metastore.timeout` | — | HMS Thrift RPC timeout (seconds) | Yes (ALTER CATALOG) |
| `background_refresh_metadata_interval_millis` | 600000 | Background refresh daemon interval (FE) | No (fe.conf restart) |
| `background_refresh_metadata_time_secs_since_last_access_secs` | 86400 | Refresh window for accessed tables | No (fe.conf restart) |

---

## Causal Chains

### Chain 1: HMS Overload → Partition Listing Slow → Query Planning Hang

```
Query references external table with large partition count (>10k partitions)
  ↓ observable: FE log shows getPartitions or listPartitions call initiated against HMS
HMS thread pool saturated by concurrent partition listing requests
  ↓ observable: HMS logs show queued RPC requests; response latency >30s per listPartitions call
FE query planner blocks waiting for partition metadata
  ↓ observable: FE log shows repeated partition fetch latency warnings; SHOW PROC '/current_queries' shows query stuck in Planning state
Query planning never completes within timeout
  ↓ observable: Client receives "ERR: query timeout"; users cannot query external tables
```
**Trigger conditions**: HMS is a shared service under heavy load; external table has high partition cardinality; no partition pruning applied.

**Break point**: Enable metadata caching (`hive.metastore.cache.ttl`); apply partition pruning predicates in query; scale HMS horizontally.

---

### Chain 2: Kerberos Ticket Expiry → Auth Failure → All External Queries Fail

```
Kerberos TGT on BE host reaches expiry time
  ↓ observable: BE log "kinit" failure: "Credentials cache file not found" or ticket expired
BE attempts HMS / HDFS RPC with expired credentials
  ↓ observable: BE log "GSS initiate failed" or "javax.security.sasl.SaslException"
HMS / HDFS rejects authentication; returns auth error to BE
  ↓ observable: FE log propagates AuthenticationException from catalog connector
All queries against Hive / HDFS external tables fail at scan phase
  ↓ observable: SHOW PROC '/current_queries' shows external scan queries failing; zero bytes scanned in loads
```
All external table queries return authentication error; internal table queries unaffected.

**Trigger conditions**: `kinit` renewal cron stopped; keytab file rotated without updating BE config; BE host clock skew exceeds Kerberos tolerance (5 min).

**Break point**: Configure automatic ticket renewal via `kinit -R` cron or `k5start`; verify keytab path in `be.conf`; ensure NTP synchronization.

---

### Chain 3: Network Saturation → Remote Scan Bandwidth Capped → Slow Despite Low CPU

```
Large external table scan initiated; BE begins reading remote HDFS/S3 blocks
  ↓ observable: BE network egress metrics show bytes/sec approaching NIC or link capacity
Network link between BE and HDFS namenode/datanode saturates
  ↓ observable: network interface counters show TX/RX near line rate; ping RTT to namenode increases
Remote scan bandwidth capped; BE read threads stall waiting for network I/O
  ↓ observable: information_schema.loads shows low bytes/sec despite high elapsed time; BE scan thread CPU near 0%
Query execution dominated by remote I/O wait
  ↓ observable: SHOW PROC '/current_queries' shows long-running external scans; local CPU on BE hosts remains low
```
External table queries slow despite BE hosts appearing idle by CPU metrics.

**Trigger conditions**: Multiple concurrent large external scans share same network uplink; HDFS co-located on same network segment with insufficient bandwidth.

**Break point**: Limit concurrent external scan parallelism; enable S3/HDFS data caching on BE to avoid repeated remote reads; upgrade network bandwidth.

---

## Related Cases

- `case-005-ssl-certificate` — private OSS SSL issue blocking Broker Load
- `case-006-network-saturation` — HMS degradation saturating CN network
- `case-016-kerberos-authentication` — Kerberos authentication failures with Hive/HDFS

---

## Resources

- [Hive catalog documentation](https://docs.starrocks.io/docs/data_source/catalog/hive_catalog/)
