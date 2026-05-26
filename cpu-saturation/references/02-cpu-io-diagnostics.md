---
type: reference
category: cpu-saturation
keywords: [perf top, iostat, offwaketime, bcc, sysctl, fd limit, ulimit, dmesg, OOM killer]
---

# 02 - CPU & IO Diagnostic Commands

---

## 6. CPU and IO Diagnostics

```bash
# CPU diagnostics — perf top for hotspots
perf top -p <be_pid>

# IO diagnostics — check disk utilization
iostat -x 1 10

# BCC tools for latency analysis
yum install bcc-tools.x86_64
offwaketime -f -U -p <be_pid>

# Check kernel network parameters
sysctl -a | grep tcp

# Check fd limit
ulimit -n
cat /proc/<be_pid>/limits

# Check dmesg (OOM Killer, etc.)
dmesg | tail -100
```

---