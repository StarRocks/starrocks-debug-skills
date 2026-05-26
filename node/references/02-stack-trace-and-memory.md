---
type: reference
category: node
keywords: [jstack, pstack, pprof, mem_tracker, memz, large memory alloc, analyze_logs.py, tcmalloc]
---

# 02 - Stack Trace & Memory Diagnostics

---

## 3. Stack Trace Capture

```bash
# FE Java stack (capture multiple times for comparison)
jstack <fe_pid> > /tmp/fe_jstack_$(date +%s).log

# BE C++ stack
pstack <be_pid> > /tmp/be_pstack_$(date +%s).log

# BE CPU profiling via pprof (60s flame graph)
pprof --svg http://<be_ip>:8060/pprof/profile?seconds=60 > cpu_profile.svg
```

---

---

## 5. Memory Diagnostics

```bash
# BE per-module memory breakdown
curl -s http://<BE_IP>:<BE_HTTP_PORT>/mem_tracker
curl -s http://<BE_IP>:<BE_HTTP_PORT>/metrics | grep "^starrocks_be_.*_mem_bytes"

# Check tcmalloc status
curl -s http://<BE_IP>:<BE_HTTP_PORT>/memz

# Find high-memory queries via mem_tracker or large memory alloc log
curl -s http://<BE_IP>:<BE_HTTP_PORT>/mem_tracker | grep "query"

# Find large memory allocations (OOM investigation)
grep "large memory alloc" be.WARNING
```

### Audit Log Analysis for TOP N Memory Queries

Use `analyze_logs.py` to find queries with highest memory consumption:

```bash
# Find top 3 BE memory consumers
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "MemCostBytes" 3 fe.audit.log

# Find top 3 FE memory consumers
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "QueryFEAllocatedMemory" 3 fe.audit.log

# Find top 3 CPU-intensive queries
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "CpuCostNs" 3 fe.audit.log

# Find top 3 scan-heavy queries
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "ScanBytes" 3 fe.audit.log
```

Available sort fields: `CpuCostNs`, `ScanBytes`, `MemCostBytes`, `QueryFEAllocatedMemory`.

---