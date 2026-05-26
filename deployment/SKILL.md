---
name: 08-deployment
description: "Use when FE or BE processes fail to start or stay alive. Covers port conflicts (YARN co-location), missing meta or storage directories, multi-NIC priority_networks misconfiguration, clock drift causing replica delta errors, BDB journal corruption blocking FE replay, HMS blocking FE startup, and SSL certificate expiry causing partial availability. Includes startup log triage for both FE and BE."
version: 2.0.0
category: deployment
keywords:
- FE startup
- BE startup
- port conflict
- BDB
- JDK
- priority_networks
- helper
- NTP
tools:
- ss
related_cases:
- case-005-ssl-certificate
---

# 08 - Deployment Troubleshooting

Investigation guide for FE and BE startup failures, port conflicts, BDB residual entries,
JDK/JRE issues, and priority network configuration.

Six root causes account for the vast majority of startup failures:

- **Cause A** — Port conflict (FE or BE port already in use)
- **Cause B** — Missing directories or meta path does not exist
- **Cause C** — Multi-NIC / `priority_networks` misconfiguration
- **Cause D** — Clock drift (NTP not synced, delta >5s)
- **Cause E** — BDB corruption or residual member entry
- **Cause F** — HMS blocking FE startup (external catalog configured but HMS unreachable)

---

## Metric Taxonomy — Read This First

Before diagnosing, understand the observable signals for startup failures.

### FE startup signals (`fe.log`)

| Signal | Meaning |
|---|---|
| `port xxx is used` | Port conflict — another process holds the port |
| `meta not exist` | FE meta directory missing — must be created before start |
| `Could not initialize class...BackendServiceProxy` | JRE installed instead of JDK; `tools.jar` missing |
| `Fe type:unknown, is ready:false` + `current node is not added` | FE not registered — run `ALTER SYSTEM ADD FOLLOWER/OBSERVER` first |
| `backend ip saved in master does not equal to backend local ip` | Wrong NIC selected — set `priority_networks` |
| `this replica exceeds max permissible delta:5000ms` | Clock skew between FE nodes >5s — sync NTP |
| `connection refused` on `http_port` | Network partition between FE nodes or wrong port in `fe.conf` |
| `Finished replaying journal` **absent** after >60s | BDB journal replay stuck or HMS blocking startup |
| `BDB environment setup failed` / `LogFileNotFoundException` | BDB corruption — requires recovery |
| `It conflicts with the socket already used by the member` | BDB sees stale member entry — `DbGroupAdmin -removeMember` |
| `Start to connect to Hive metastore` + no `Connected` | HMS unreachable, blocking FE catalog init |

### BE startup signals (`be.log`)

| Signal | Meaning |
|---|---|
| `port xxx is used` | Port conflict on BE (thrift, brpc, webserver, or heartbeat port) |
| `storage not exist` | Storage path in `be.conf` `storage_root_path` does not exist |
| `Be http service did not start correctly` | Webserver port conflict (YARN often uses 8088/8042) |
| `Could not initialize class...BackendServiceProxy` | JRE instead of JDK |
| `load tablet from header failed` | Corrupted tablet header — requires `meta_tool` cleanup |

### Network / system checks

| Command | What it shows |
|---|---|
| `ss -lntp \| grep <port>` | Which process holds a given port |
| `telnet <host> <port>` | TCP reachability between nodes |
| `timedatectl` | NTP sync status and current time offset |
| `ntpstat` | NTP synchronization state and stratum |
| `DbGroupAdmin -listMembers` | Current BDB group membership (stale entries visible here) |

### How to retrieve signals

**Option 1 — FE log grep**

```bash
# Check startup failure reason
grep -E "port.*is used|meta not exist|BackendServiceProxy|is ready:false|replaying journal|BDB.*fail" fe.log | tail -30

# Check clock skew
grep "permissible delta" fe.log | tail -10

# Check HMS blocking
grep -E "connect.*metastore|Finished replaying journal" fe.log | tail -20
```

**Option 2 — BE log grep**

```bash
# Check BE startup failure reason
grep -E "port.*is used|storage not exist|http service|load tablet.*failed" be.log | tail -20
```

**Option 3 — Port occupancy**

```bash
# Find what holds a specific port
ss -lntp | grep <port>
# e.g.: ss -lntp | grep 9030

# Check all common StarRocks ports
for port in 8030 9020 9030 9010 8040 9050 9060 8060; do
    echo -n "Port $port: "
    ss -lntp | grep ":$port " || echo "free"
done
```

**Option 4 — Clock and time**

```bash
# Check NTP sync status
timedatectl status
# Look for: "NTP synchronized: yes"

# Check time delta across nodes (run from one host, compare outputs)
date && ssh <fe2_host> date && ssh <fe3_host> date

# ntpstat: shows stratum and offset
ntpstat
```

**Option 5 — BDB member status**

```bash
# List BDB group members (run on FE host)
java -jar $STARROCKS_HOME/fe/lib/starrocks-bdb-je-*.jar \
  com.sleepycat.je.util.DbGroupAdmin \
  -groupName StarRocks \
  -nodeName <fe_node_name> \
  -nodeHostPort <fe_host>:<bdb_port> \
  -listMembers

# Remove stale member
# -removeMember <stale_member_name>
```

---

**Key rules for interpretation**:

1. If `Finished replaying journal` never appears, distinguish between BDB stuck (Cause E) and HMS blocking (Cause F) by checking whether `Start to connect to Hive metastore` appears before the hang.
2. `port xxx is used` can be any of FE's 4 ports (http/rpc/query/edit) — check all: 8030, 9010, 9020, 9030.
3. Clock skew errors reference a 5-second threshold; even 4.9s drift can cause intermittent `ready:false`.
4. BDB residual member entries persist across restarts; `DbGroupAdmin -removeMember` is required even after the old FE host is decommissioned.

---

## Startup Issue Reference

| Component | Symptom | Log keyword | Fix |
|---|---|---|---|
| FE | Process not found | `port xxx is used` | Change port in `fe.conf`; kill conflicting process |
| FE | Process not found | `meta not exist` | Create meta directory under FE install path |
| FE | Process not found | `Could not initialize class...BackendServiceProxy` | Install JDK (not JRE); Oracle JDK 1.8+ |
| FE | `alive=false` | `Fe type:unknown, is ready:false` + `current node is not added` | `ALTER SYSTEM ADD FOLLOWER/OBSERVER "host:port"` first |
| FE | `alive=false` | `backend ip saved in master does not equal to backend local ip` | Set `priority_networks` in `fe.conf` |
| FE | `alive=false` | `this replica exceeds max permissible delta:5000ms` | Sync clocks via NTP; max allowed delta is 5s |
| FE | `alive=false` | `connection refused` | Check network between FEs on `http_port` |
| FE | `false` on other FEs | Follower started without `--helper` | Clear meta; restart with `--helper host:port` |
| FE | Stuck `is ready:false`, large BDB | BDB folder growing, no checkpoint | Increase FE JVM heap in `JAVA_OPTS`; restart |
| FE | Startup hang, no `Finished replaying` | `It conflicts with the socket already used by the member` | `DbGroupAdmin -removeMember` to clean residual entries |
| FE | Startup hang, no `Finished replaying` | `Start to connect to Hive metastore` with no response | Check HMS reachability; temporarily remove catalog config |
| BE | Process not found | `port xxx is used` | Change conflicting port in `be.conf` |
| BE | Process not found | `storage not exist` | Create storage directory listed in `storage_root_path` |
| BE | Process not found | `Be http service did not start correctly` | Change webserver port; check YARN port conflicts |
| BE | Process not found | `Could not initialize class...BackendServiceProxy` | Install JDK, not JRE |
| BE | Process not found | `load tablet from header failed` | Use `meta_tool` to clean corrupted tablet |
| BE | Not visible in `SHOW BACKENDS` | — | `ALTER SYSTEM ADD BACKEND "host:port"` (heartbeat port, default 9050) |
| BE | Unavailable | `Failed to get scan range, no queryable replica found` | Disk full — add storage paths in `be.conf` `storage_root_path` |

---

## Phase 1 — Identify Which Component Fails to Start

### Step 1.1 — Determine what failed

```bash
# Check if FE process is running
pgrep -f StarRocksFE || echo "FE not running"

# Check if BE process is running
pgrep -f starrocks_be || echo "BE not running"

# Check FE alive status via SQL (run from another FE)
# SHOW FRONTENDS;   -- Look for: Alive=false, Role=UNKNOWN

# Check BE visibility
# SHOW BACKENDS;    -- Look for: Alive=false or missing entries
```

### Step 1.2 — Read the startup log for the failed component

```bash
# FE: find the failure reason
grep -E "ERROR|WARN|port.*is used|meta not exist|BackendServiceProxy|is ready:false|replaying|BDB" \
    fe/log/fe.log | head -50

# BE: find the failure reason
grep -E "ERROR|WARN|port.*is used|storage not exist|http service|load tablet" \
    be/log/be.log | head -50
```

**Log keyword → Cause mapping**:

| Log keyword | Cause |
|---|---|
| `port xxx is used` | Cause A |
| `meta not exist` / `storage not exist` | Cause B |
| `backend ip saved in master` / `priority_networks` | Cause C |
| `permissible delta` / `clock skew` | Cause D |
| `BDB.*fail` / `conflicts with the socket already used` | Cause E |
| `Start to connect to Hive metastore` + startup hang | Cause F |

---

## Phase 2 — Find Root Cause from Logs

### Step 2.1 — Check port conflicts (Cause A signal)

```bash
# Identify what holds the conflicting port
ss -lntp | grep <port_number>

# Check common ports for FE
ss -lntp | grep -E ":8030|:9010|:9020|:9030"

# Check common ports for BE
ss -lntp | grep -E ":8040|:9050|:9060|:8060"
```

### Step 2.2 — Check directories (Cause B signal)

```bash
# FE meta directory
grep "meta_dir" fe/conf/fe.conf
ls -la <meta_dir>  # Should exist; if not → create it

# BE storage path
grep "storage_root_path" be/conf/be.conf
ls -la <storage_path>  # Should exist; if not → create it
```

### Step 2.3 — Check clock sync (Cause D signal)

```bash
timedatectl status
# Look for: "NTP synchronized: yes"
# If no: systemctl start chronyd

# Compare time across FE hosts
date && ssh <fe2> date  # Difference should be <5s
```

### Step 2.4 — Check BDB state (Cause E signal)

```bash
# Look for BDB error in fe.log
grep -E "BDB.*fail|LogFileNotFoundException|ReplicaConsistencyException|conflicts with the socket" \
    fe/log/fe.log | tail -10

# Check BDB directory size (abnormally large = no checkpoint)
du -sh fe/meta/bdb/
```

### Step 2.5 — Check HMS reachability (Cause F signal)

```bash
# Is HMS configured?
grep -i "hive\|metastore" fe/conf/fe.conf | head -10

# Is HMS reachable?
telnet <hms-host> <hms-port>
# Timeout/refused → Cause F
```

---

## Phase 3 — Take Action by Cause

### Cause A — Port Conflict

**Confirm all match**:
- `fe.log` or `be.log` shows `port xxx is used`
- `ss -lntp | grep <port>` shows another process holding the port

**Mechanism**: StarRocks FE binds multiple ports at startup (http, rpc, query, edit log). BE binds thrift, brpc, webserver, and heartbeat ports. If any port is in use — commonly by YARN NodeManager (8042, 8088), or a previous StarRocks instance that did not exit cleanly — the process exits immediately without completing startup.

**Actions**:

```bash
# Step A-1: Identify the conflicting process
ss -lntp | grep <port>
# Output shows PID; kill or move the conflicting process

# Step A-2: Change the port in config (if process cannot be moved)
# FE: edit fe/conf/fe.conf
# BE: edit be/conf/be.conf
# Then restart

# Step A-3: Kill lingering StarRocks process
kill -9 $(pgrep -f StarRocksFE)   # FE
kill -9 $(pgrep starrocks_be)      # BE
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause B — Missing Directories / Meta

**Confirm all match**:
- `fe.log` shows `meta not exist` OR `be.log` shows `storage not exist`
- `ls <meta_dir>` or `ls <storage_path>` returns "No such file or directory"

**Mechanism**: FE requires a `meta/` directory (default `$STARROCKS_HOME/fe/meta`) to initialize BDB journal storage. BE requires each `storage_root_path` directory to exist before it can mount tablet storage. These directories are never created automatically by StarRocks.

**Actions**:

```bash
# Step B-1: Create FE meta directory
mkdir -p $(grep meta_dir fe/conf/fe.conf | cut -d= -f2 | tr -d ' ')
# Or use default:
mkdir -p fe/meta

# Step B-2: Create BE storage directory
# For each path listed in storage_root_path:
mkdir -p /data/starrocks/storage

# Step B-3: Set correct ownership if running as non-root
chown -R starrocks:starrocks /data/starrocks/storage
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause C — Multi-NIC / priority_networks Misconfiguration

**Confirm all match**:
- `fe.log` shows `backend ip saved in master does not equal to backend local ip`
- Host has multiple network interfaces (`ip addr show` shows multiple non-loopback IPs)

**Mechanism**: When a host has multiple NICs, StarRocks selects the first non-loopback IP by default, which may be a management or storage network rather than the intended cluster network. The `priority_networks` setting restricts IP selection to a specific CIDR subnet, ensuring all nodes use the same network for cluster communication.

**Actions**:

```bash
# Step C-1: List available network interfaces
ip addr show | grep "inet " | grep -v "127.0.0"

# Step C-2: Identify the correct cluster network CIDR
# e.g., if cluster uses 10.0.1.x, the CIDR is 10.0.1.0/24

# Step C-3: Add priority_networks to fe.conf and be.conf
echo "priority_networks = 10.0.1.0/24" >> fe/conf/fe.conf
echo "priority_networks = 10.0.1.0/24" >> be/conf/be.conf

# Step C-4: Restart FE/BE
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause D — Clock Drift (NTP Not Synced)

**Confirm all match**:
- `fe.log` shows `this replica exceeds max permissible delta:5000ms`
- `timedatectl` shows `NTP synchronized: no` OR time difference between hosts >5s

**Mechanism**: StarRocks FE uses BDB replication which requires synchronized clocks. The allowable delta is 5000ms (5 seconds). When clock drift exceeds this threshold, BDB rejects the replica's journal entries, causing the FE to stay in `is ready:false` state indefinitely.

**Actions**:

```bash
# Step D-1: Check NTP status on all FE hosts
timedatectl status
ntpstat

# Step D-2: Start/restart NTP service
systemctl start chronyd
# Or: systemctl start ntpd

# Step D-3: Force immediate time sync
chronyc makestep
# Or: ntpdate -u <ntp_server>

# Step D-4: Verify all hosts are in sync
# Run on each host and compare output:
date +%s
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause E — BDB Corruption or Residual Member

**Confirm all match**:
- `fe.log` shows `BDB environment setup failed`, `LogFileNotFoundException`, `ReplicaConsistencyException`, OR `It conflicts with the socket already used by the member`
- FE startup hangs at BDB init and `Finished replaying journal` never appears

**Mechanism**: BDB JE (Berkeley DB Java Edition) stores FE metadata journal. Unclean FE exits (OOM, kill -9, power loss) can leave truncated or checksum-mismatched journal entries. Additionally, when an FE node is removed from the cluster without proper deregistration, BDB retains its membership entry. The next FE startup conflicts with the stale entry, causing it to fail socket binding.

**Actions**:

```bash
# For residual member conflict:
# Step E-1: List BDB group members
# Run DbGroupAdmin to identify the stale member name

# Step E-2: Remove stale member
java -jar $STARROCKS_HOME/fe/lib/starrocks-bdb-je-*.jar \
  com.sleepycat.je.util.DbGroupAdmin \
  -groupName StarRocks \
  -nodeName <this_fe_node_name> \
  -nodeHostPort <this_fe_host>:<bdb_port> \
  -removeMember <stale_member_name>

# For BDB corruption:
# Step E-3: Use bdb_tool to inspect and truncate at last clean checkpoint
# (Refer to StarRocks documentation for bdb_tool usage)

# Step E-4: If corruption is severe — promote a healthy follower FE to leader
# Or restore from FE metadata backup (fe/meta/image/ directory snapshot)

# Step E-5: Also try increasing FE heap size to prevent future checkpoint failures
# In fe/conf/fe.conf JAVA_OPTS: -Xms8g -Xmx8g
```

→ **Go to Phase 4: Verify Recovery**

---

### Cause F — HMS Blocking FE Startup

**Confirm all match**:
- `fe.log` shows `Start to connect to Hive metastore: <host>:<port>`
- `Finished replaying journal` never appears after HMS connection attempt
- `telnet <hms-host> <hms-port>` fails or hangs

**Mechanism**: When a Hive external catalog is configured in `fe.conf`, FE initializes the catalog module during startup, which attempts a TCP connection to HMS. If HMS is unreachable and no connection timeout is configured, this call blocks indefinitely. FE never completes the startup sequence and remains unresponsive.

**Actions**:

```bash
# Step F-1: Verify HMS is unreachable
telnet <hms-host> <hms-port>
# Timeout confirms the block

# Step F-2: Temporary fix — remove catalog config from fe.conf
# Comment out the external catalog configuration block
# Restart FE — it should now complete startup

# Step F-3: Fix HMS service (independent of StarRocks)
# Verify HMS process is running on HMS host
# Check HMS port, firewall rules

# Step F-4: Add HMS connection timeout to prevent future blocking
# After FE is running, set timeout in catalog config:
```

```sql
-- Add connection timeout to catalog
ALTER CATALOG <catalog_name> SET PROPERTIES (
    "hive.metastore.timeout" = "10"
);
```

→ **Go to Phase 4: Verify Recovery**

---

## Phase 4 — Verify Recovery

```sql
-- Check FE status (all should show Alive=true)
SHOW FRONTENDS;
-- Key fields: Role (LEADER/FOLLOWER/OBSERVER), Alive=true, JoinTime should be recent

-- Check BE status
SHOW BACKENDS;
-- Key fields: Alive=true, LastStartTime should be recent, TabletNum > 0 (if tablets exist)
```

```bash
# Verify FE HTTP port is responding
curl -s http://<fe_host>:8030/api/health
# Should return: {"status":"ok",...}

# Verify BE heartbeat port is responding
curl -s http://<be_host>:8040/api/health
# Should return: {"status":"ok",...}

# Verify FE completed startup (journal replayed)
grep "Finished replaying journal" fe/log/fe.log | tail -3
# Should show a recent timestamp

# Verify no port conflicts remain
ss -lntp | grep -E ":8030|:9030|:8040|:9050"
# Each port should show StarRocks process as owner
```

```sql
-- Run a simple query to confirm cluster is operational
SELECT COUNT(*) FROM information_schema.tables LIMIT 1;

-- Verify external catalog is accessible (if Cause F was fixed)
SHOW CATALOGS;
SELECT COUNT(*) FROM <catalog_name>.<db_name>.<table_name> LIMIT 1;
```

---

## Causal Chains

### Chain 1: HMS Unreachable → FE Startup Blocks Indefinitely

```
FE process starts; begins loading catalog configurations from fe.conf
  ↓ observable: fe.log shows "Start to connect to Hive metastore: <host>:<port>"
FE catalog module attempts TCP connection to configured HMS endpoint
  ↓ observable: fe.log shows connection attempt with no response; no "Connected to HMS" confirmation
Connection attempt blocks on socket timeout (may be very long or infinite)
  ↓ observable: fe.log shows no progress for >60s after HMS connection attempt; FE process alive but unresponsive
FE startup sequence stalls before completing metadata replay
  ↓ observable: "Finished replaying journal" line never appears; FE never enters LEADER/FOLLOWER state
Cluster completely unavailable
```
FE HTTP port does not respond; all client connections refused.

**Trigger conditions**: Hive catalog added to `fe.conf` but HMS service down or misconfigured; firewall blocks HMS port; HMS hostname unresolvable from FE host.

**Break point**: Set short HMS connection timeout in catalog config; verify HMS reachability (`telnet <hms_host> <port>`) before starting FE; temporarily remove catalog config to allow FE to start.

---

### Chain 2: Expired SSL Certificate → Partial Availability Window

```
SSL certificate on FE/BE HTTPS endpoint reaches expiry date
  ↓ observable: openssl s_client -connect <host>:<port> shows "Verify return code: 10 (certificate has expired)"
New client connections fail TLS handshake immediately
  ↓ observable: client-side errors: "SSL handshake failed"; FE log shows SSLHandshakeException
Existing long-lived connections (JDBC pool, internal FE-BE RPC) continue normally
  ↓ observable: some queries succeed (pooled connections); new connection attempts from fresh clients fail
Partial availability: some clients succeed, others fail depending on connection age
  ↓ observable: non-zero but non-100% error rate; no single point of failure in cluster health dashboard
```
Intermittent connectivity failures; diagnosis confused by partial success; issue appears non-deterministic.

**Trigger conditions**: Certificate renewal automation has failed silently; manual certificate installed without a renewal reminder.

**Break point**: Rotate and install renewed certificate; restart FE/BE to force all connections through new TLS context; add certificate expiry monitoring alert (30-day warning threshold).

---

### Chain 3: BDB Journal Corruption → FE Cannot Replay Metadata → Permanent Startup Failure

```
FE exits abnormally (OOM, kill -9, power loss) while writing BDB journal
  ↓ observable: no clean shutdown entry in fe.log; OS-level evidence of abrupt termination
FE restarts and attempts to replay BDB journal from last checkpoint
  ↓ observable: fe.log shows "BDB environment setup" or "replaying journal from seq <N>"
BDB detects checksum mismatch or truncated record
  ↓ observable: fe.log shows "BDB environment setup failed", "LogFileNotFoundException", or "ReplicaConsistencyException"
Journal replay aborts; FE cannot reconstruct in-memory metadata
  ↓ observable: FE process exits or loops in restart; "Finished replaying journal" never appears
FE permanently unable to start without manual recovery
```
Entire cluster loses FE availability.

**Trigger conditions**: Unclean FE shutdown under high write load; disk I/O errors during journal write; BDB version mismatch after upgrade.

**Break point**: Use `bdb_tool` to inspect and truncate corrupted journal entries to last clean checkpoint; promote a healthy follower FE to leader; restore from recent FE metadata backup (`image/` directory snapshot).

---

## Related Cases

- `case-005-ssl-certificate` — SSL handshake error against private object storage during Broker Load

---

## Resources

- [Operation & Maintenance FAQ](https://docs.starrocks.io/docs/faq/operation_maintenance_faq)
