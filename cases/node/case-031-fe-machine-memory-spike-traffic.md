---
type: case
category: node
issue: fe-machine-memory-spike-traffic
keywords: [fe, memory, traffic, arrow-flight, direct-memory, proxy, network-pressure]
---

# Case-031: FE Machine Memory Spike Due to Arrow Flight Traffic Surge

## Environment

- StarRocks version: All versions
- Architecture: shared-nothing / shared-data
- Configuration: arrow_flight_proxy_enabled = true (default), arrow_flight_proxy = '' (default)

## Symptom

**Symptoms observed:**
- FE machine memory suddenly decreases (available memory drops)
- Indicates sudden memory increase on FE node
- FE node experiences traffic surge simultaneously

## Investigation

### Step 1: Check Machine Memory

```
Available memory: Sudden decrease
Traffic: Sudden increase
```

### Step 2: Check Traffic Source

**Normal behavior:** FE nodes typically do not have significant traffic.

If large traffic observed, check:
- Were Arrow Flight queries executed at that time?

### Step 3: Understand Arrow Flight Proxy Behavior

**Default configuration behavior:**
- `arrow_flight_proxy_enabled = true`
- `arrow_flight_proxy = ''`

**Result:**
- Arrow Flight query results are proxied through the FE node client initially connected to
- Large query results cause traffic surge
- Proxy forwarding causes additional FE off-heap (Direct Memory) usage
- Pushes up machine memory occupation

## Root Cause

**Arrow Flight proxy forwarding memory overhead:**

1. **Proxy behavior**: Query results proxied through initial FE connection
2. **Large results**: Big query results cause traffic surge
3. **Direct Memory**: Proxy forwarding uses additional Direct Memory
4. **Machine memory**: Direct Memory pushes up machine memory usage

## Resolution

### Disable Arrow Flight Proxy

**When to apply:**
- Machine memory insufficient
- FE network pressure high
- Reduce FE traffic and memory pressure
- Reduce FE OOM risk

**Configuration:**

```sql
SET GLOBAL arrow_flight_proxy_enabled = false;
```

**Effect:**
- Query results returned directly from BE to client
- Reduces FE traffic and memory pressure

**Important notes:**

1. **Network connectivity**: Before disabling, ensure client can connect to BE Arrow Flight port, otherwise queries may fail
2. **GLOBAL setting**: Must use SET GLOBAL, not session variable

## Key Commands

```bash
# Check machine memory
free -m

# Check network traffic
iftop -i eth0

# Check FE process memory
ps aux | grep fe

# Check Direct Memory
jcmd <FE_pid> VM.native_memory summary
```

```sql
-- Check Arrow Flight session variable
SHOW VARIABLES LIKE 'arrow_flight_proxy_enabled';

-- Disable Arrow Flight proxy (global setting)
SET GLOBAL arrow_flight_proxy_enabled = false;

-- Verify configuration
SHOW VARIABLES LIKE 'arrow_flight_proxy_enabled';
```

## Lessons Learned

1. **Arrow Flight proxy**: Default proxy behavior can cause FE memory/traffic pressure
2. **Direct Memory impact**: Proxy forwarding uses Direct Memory, affecting machine memory
3. **Network connectivity**: Must ensure client-BE connectivity before disabling proxy
4. **Traffic management**: Consider disabling proxy for large query result scenarios

---

## Related Skills

- [03-node.md](../../skills/03-node.md) — FE/BE node troubleshooting
- [case-030-fe-machine-memory-growth-malloc-arena.md](case-030-fe-machine-memory-growth-malloc-arena.md) — Machine memory growth due to malloc arena