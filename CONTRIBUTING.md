---
type: contributing
description: How to add and maintain content in this project
---

# Contributing Guide

How to add cases, skills, and reference documents.

---

## Project Structure

```
starrocks-debug-skills/
├── README.md
├── LICENSE
├── CONTRIBUTING.md             # This file
├── guides/                     # Cross-skill cascade guides
│   └── cascade-<name>.md
├── references/                 # Shared reference docs (common log/cluster commands)
│   └── 01-common-commands.md
├── scripts/
│   ├── install.sh              # Install skills to IDEs
│   ├── skill_converter.py      # Verify structure and convert to IDE formats
│   └── analyze_logs.py         # Audit log analysis (also copied per-skill where needed)
├── .claude/
│   └── settings.json           # Claude Code permissions (do not add skill data here)
├── <category>/                 # One directory per skill
│   ├── SKILL.md                # Skill document (main entry point)
│   ├── references/             # Per-skill focused reference docs and cases
│   │   ├── <NN>-<name>.md      # Focused knowledge doc (e.g. 02-profile-and-sql-toolkit.md)
│   │   └── case-<number>-<name>.md
│   └── scripts/                # Skill-specific helper scripts (optional)
└── ...
```

Skill categories: `query` `import` `node` `materialized-view` `data-lake`
`shared-data` `tablet` `deployment` `high-concurrency` `resource-isolation`
`balance` `compaction` `cpu-saturation`

---

## Adding Content

### Add a Case to an Existing Skill

1. Create `<category>/references/case-<number>-<short-name>.md`
2. Use the frontmatter template below.
3. If the case is relevant to other skills, reference it by name in those skills' SKILL.md `Related Cases` section — do not copy the file.

### Add a Focused Reference Doc to an Existing Skill

1. Create `<category>/references/<NN>-<name>.md` (e.g. `02-profile-and-sql-toolkit.md`).
2. Scope the content to that skill's domain — do not duplicate content from another skill's reference doc.
3. Reference it from the skill's SKILL.md where appropriate.

### Add a Cross-Skill Cascade Guide

1. Create `guides/cascade-<name>.md`.
2. Add a row to the Cross-Skill Guides table in `README.md`.
3. Add a `## Cross-Skill Guides` section referencing the guide in each SKILL.md that is a key stage in the cascade.

### Add a New Skill

1. Create `<new-category>/SKILL.md` using the SKILL.md frontmatter template below.
2. Add `references/` and optionally `scripts/` subdirectories.
3. Add the new category name to `SKILL_CATEGORIES` in `scripts/skill_converter.py`.
4. Add a row to the Skills Categories table in `README.md`.

---

## YAML Frontmatter Templates

### SKILL.md

```yaml
---
name: <category>               # matches directory name, e.g. cpu-saturation
description: >
  Use when <symptom or trigger condition>. Covers <key scenarios>.
version: 1.0.0
category: <category>
keywords:
  - keyword1
  - keyword2
tools:
  - grep
  - jstack
related_cases:
  - case-001-example
---
```

### Case file (`references/case-<number>-<name>.md`)

```yaml
---
type: reference
category: <category>
issue: <short-issue-slug>          # e.g. fe-deadlock
keywords: [keyword1, keyword2]
---
```

### Focused reference doc (`references/<NN>-<name>.md`)

```yaml
---
type: reference
category: <category>
keywords: [keyword1, keyword2]
---
```

---

## Naming Conventions

- **Skill documents**: `<category>/SKILL.md`
- **Case files**: `case-<number>-<short-name>.md` (e.g. `case-001-broker-load-backlog.md`)
- **Focused reference docs**: `<NN>-<name>.md` (e.g. `02-profile-and-sql-toolkit.md`) — number reflects the reading order within that skill

Case numbers are global across all categories — check the highest existing number before assigning a new one.

---

## Shared Cases

A case that is relevant to multiple skills should live in **one canonical skill** directory only. Other skills reference it by name in their SKILL.md `Related Cases` section. Do not copy case files across skill directories.

**Canonical locations for shared cases:**

| Case | Canonical skill |
|---|---|
| `case-003-fe-deadlock` | `node/references/` |
| `case-004-disk-balancing` | `tablet/references/` |
| `case-007-memory-tracking-leak` | `node/references/` |
| `case-008-be-oom` | `node/references/` |
| `case-014-scan-skew` | `query/references/` |
| `case-015-memory-volatility` | `high-concurrency/references/` |

---

## Quality Guidelines

1. No customer names (CI enforced).
2. English only (CI enforced).
3. All SKILL.md files must have `name`, `category`, `keywords` frontmatter (CI enforced).
4. All reference files must have `type`, `category`, `keywords` frontmatter (CI enforced).
5. Case files must also have `issue` frontmatter (CI enforced).
6. Test commands before adding them.
7. Do not duplicate case files — use shared-case convention above.
8. Keep reference docs scoped to the skill's domain — don't replicate content across skills.

---

## After Making Changes

```bash
# Verify structure and update .idea/ config
python scripts/skill_converter.py --sync

# Install updated skills to Claude Code
./scripts/install.sh --tool claude-code --target ~/.claude/skills/
```
