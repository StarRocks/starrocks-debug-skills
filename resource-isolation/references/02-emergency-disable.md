---
type: reference
category: resource-isolation
keywords: [disable statistics, enable_statistic_collect, analyze_mv, emergency, BRPC saturation]
---

# 02 - Emergency Operations: Disable Statistics Collection

---

## 17. Disable Statistics Collection (Emergency)

```sql
ADMIN SET FRONTEND CONFIG("enable_statistic_collect" = "false");
ADMIN SET FRONTEND CONFIG("enable_statistic_collect_on_first_load" = "false");
SET GLOBAL analyze_mv = "";  -- v3.3+
```

---

## Usage

This document is intended for quick copy-paste during live debugging. Pair it with the
relevant skill file for context:

- For query / scan / join issues, see `skills/01-query.md`.
- For import / RPC / publish issues, see `skills/02-import.md`.
- For BE OOM / crash / FE deadlock, see `skills/03-node.md`.
- For MV refresh failures / rewrite issues, see `skills/04-materialized-view.md`.
- For shared-data and DataCache issues, see `skills/06-shared-data.md`.

If your environment exposes MCP tools for log search, metric queries, or remote command
execution, use them in place of `grep`/`curl` against individual hosts. The patterns above
remain the same; only the transport changes.