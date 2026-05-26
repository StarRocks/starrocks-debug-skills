# StarRocks Debug Skills — Scripts

Utility scripts for installing and syncing StarRocks debug skills.

## Scripts

| Script | Description |
|---|---|
| `install.sh` | Install skills to IDEs (Claude Code, Cursor, JetBrains) |
| `skill_converter.py` | Verify structure, update .idea/ config, convert to IDE formats |
| `analyze_logs.py` | Audit log analysis for large queries by resource field |

`analyze_logs.py` is also copied into `query/scripts/`, `node/scripts/`, and
`cpu-saturation/scripts/` so each skill can reference it with a relative path.

## Directory Structure

```
starrocks-debug-skills/
├── guides/                     # Cross-skill cascade guides
├── references/                 # Shared reference docs (common commands)
│   └── 01-common-commands.md
├── scripts/                    # This directory
│   ├── install.sh
│   ├── skill_converter.py
│   └── analyze_logs.py
├── <category>/
│   ├── SKILL.md
│   ├── references/             # Per-skill focused docs and cases
│   │   ├── <NN>-<name>.md
│   │   └── case-<number>-<name>.md
│   └── scripts/                # Skill-specific scripts (optional)
└── ...
```

## Usage

### Install to IDE

```bash
# Claude Code
./scripts/install.sh --tool claude-code --target ~/.claude/skills/

# Cursor
./scripts/install.sh --tool cursor --target .cursor/rules/

# JetBrains
./scripts/install.sh --tool jetbrains --target .idea/

# All IDEs at once
./scripts/install.sh --tool all --target .
```

### Verify Structure

```bash
python scripts/skill_converter.py --sync
```

### analyze_logs.py

Find top N queries by resource consumption from an FE audit log:

```bash
# Top 3 by BE memory (MemCostBytes)
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "MemCostBytes" 3 fe.audit.log

# Top 3 by FE memory (QueryFEAllocatedMemory)
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "QueryFEAllocatedMemory" 3 fe.audit.log

# Top 3 by CPU (CpuCostNs)
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "CpuCostNs" 3 fe.audit.log

# Top 3 by scan bytes (ScanBytes)
python3 scripts/analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "ScanBytes" 3 fe.audit.log
```

## Requirements

- Python 3.6+
- PyYAML (`pip install pyyaml`)
