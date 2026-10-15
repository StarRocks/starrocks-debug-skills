---
name: starrocks-debug-skills
description: >
  Use when diagnosing StarRocks slow queries, ingestion failures, node crashes,
  memory or CPU pressure, data lake access, or shared-data issues.
category: diagnostics
keywords: [starrocks, debug, troubleshoot]
---

# StarRocks Debug Skills

Select the matching category from the [skills table](README.md#skills-categories)
and read its `<category>/SKILL.md` for the investigation workflow. Load additional
categories as the investigation crosses into their domain.

- Use the [common commands](references/01-common-commands.md) for cluster status,
  logs, and profiles.
- For compaction backlog followed by slow ingestion, BE OOM, or FE deadlock,
  follow the [cluster degradation cascade](guides/cascade-cluster-degradation.md).
- For import RPC failures during statistics collection, follow the
  [statistics cascade](guides/cascade-import-rpc-failed.md).
