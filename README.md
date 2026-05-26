---
type: readme
description: StarRocks cluster diagnostics and troubleshooting skill
---

# starrocks-debug-skills

StarRocks cluster diagnostics and troubleshooting skill system.

## Overview

This skill systematizes troubleshooting experience to help provide structured investigation guidance for cluster issues, covering common production problems with complete investigation paths from symptom to root cause.

## Directory Structure

```
starrocks-debug-skills/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── guides/                     # Cross-skill cascade guides
│   ├── cascade-cluster-degradation.md
│   └── cascade-import-rpc-failed.md
├── references/                 # Shared commands used across all skills
│   └── 01-common-commands.md
├── scripts/                    # Utility scripts
│   ├── install.sh              # Install skills to IDEs
│   ├── skill_converter.py      # Verify structure, convert to IDE formats
│   └── analyze_logs.py         # Audit log analysis script
├── query/                      # Query troubleshooting
│   ├── SKILL.md
│   ├── references/
│   │   ├── 02-profile-and-sql-toolkit.md
│   │   ├── 03-statistics-index.md
│   │   └── case-014-scan-skew.md
│   └── scripts/
│       └── analyze_logs.py
├── import/                     # Import troubleshooting
├── node/                       # BE/FE node issues
├── materialized-view/          # MV troubleshooting
├── data-lake/                  # External catalog issues
├── shared-data/                # Shared-data (lake-mode) architecture
├── tablet/                     # Tablet health issues
├── deployment/                 # FE/BE startup and configuration
├── high-concurrency/           # High QPS optimization
├── resource-isolation/         # Resource groups and query queues
├── balance/                    # Tablet scheduler and cluster balance
├── compaction/                 # Compaction issues
├── cpu-saturation/             # CPU triage and attribution
├── .claude/
├── .cursor/
└── .idea/
```

Each skill directory contains:
- **SKILL.md** — Structured troubleshooting instructions (main entry point)
- **references/** — Per-skill focused reference docs and case studies
- **scripts/** — Skill-specific helper scripts (query/, node/, cpu-saturation/ only)

## Skills Categories

| Category | Keywords | Description |
|---|---|---|
| query | hang, slow, profile, scan, join | Query troubleshooting, slow query analysis |
| import | timeout, RPC failed, publish | Import troubleshooting, broker/stream/routine load |
| node | crash, OOM, deadlock, GC | FE/BE node issues, memory analysis |
| materialized-view | refresh, timeout, rewrite | MV refresh failures, query rewrite |
| data-lake | HMS, Kerberos, external | External catalog, Hive, Iceberg, S3 |
| shared-data | DataCache, S3, leader switch | Shared-data (lake-mode) deployments only |
| tablet | health, balance, skew | Tablet health, disk balancing |
| deployment | startup, port, BDB, JDK | FE/BE startup failures, configuration |
| high-concurrency | QPS, connection pool | High QPS optimization, throughput plateaus |
| resource-isolation | resource group, queue | Resource groups, query queues, circuit breakers |
| balance | scheduler, clone, decommission | Tablet scheduler, cluster balance |
| compaction | version, score, rowset | Compaction issues, version count |
| cpu-saturation | CPU, load average, attribution | CPU triage — identify which workload is consuming CPU |

## Core Methodology

**"Restore in 10 minutes, root-cause within hours"**

1. **Top-down investigation** — Client → FE → BE → Storage/Network
2. **Data-driven** — Backed by logs, metrics, stack traces
3. **Mitigate first** — Service recovery via parameter tuning
4. **Binary exclusion** — Disable features via session variables

## Quick Diagnosis Commands

```sql
-- Cluster status
SHOW BACKENDS;
SHOW FRONTENDS;
SHOW PROC '/current_queries';
SHOW LOAD;
```

```bash
# Log search
grep "<query_id>" fe.log
grep "<instance_id>" be.INFO
grep -E "ERROR|WARN" fe.log | tail -100

# Analyze audit logs for large queries (also available per-skill under <skill>/scripts/)
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "MemCostBytes" 3 fe.audit.log
```

## Installation

### Claude Code

```bash
./scripts/install.sh --tool claude-code --target ~/.claude/skills/
```

### Cursor IDE

```bash
./scripts/install.sh --tool cursor --target .cursor/rules/
```

### JetBrains IDEs

```bash
./scripts/install.sh --tool jetbrains --target .idea/
```

## Trigger Conditions

Triggered when conversation contains:
- Investigation terms: debug, troubleshoot, diagnose, root cause
- Symptoms: slow query, import failure, OOM, crash, timeout, hang
- Tool terms: jstack, pstack, profile, Grafana

## Cross-Skill Guides

Multi-skill cascade scenarios that span more than one troubleshooting domain:

| Guide | Pattern |
|---|---|
| [Cluster Degradation Cascade](guides/cascade-cluster-degradation.md) | Compaction backlog → Import slow → BE OOM → FE Deadlock |
| [Import RPC Failed from Statistics](guides/cascade-import-rpc-failed.md) | Statistics collection → BRPC starvation → Import/Query failure |

## Maintenance

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add:
- New skills → `<category>/SKILL.md`
- New cases → `<category>/references/case-<number>-<name>.md`
- New focused reference docs → `<category>/references/<name>.md`
- New cross-skill guides → `guides/<name>.md`
