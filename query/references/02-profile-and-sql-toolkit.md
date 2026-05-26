---
type: reference
category: query
keywords: [profile, SHOW PROFILELIST, ANALYZE PROFILE, EXPLAIN, query dump, session variables, feature disable]
---

# 02 - Query Diagnostics: Profile & SQL Toolkit

---

## 2. Profile Collection

```bash
# Fetch profile via HTTP API
curl --location-trusted -u root: \
  "http://<MASTER_FE_IP>:<FE_HTTP_PORT>/query_profile?query_id=<query_id>" > profile.txt

# Find query ID from audit log
grep "QueryId" fe.audit.log | grep "<sql_keyword>"
```

```sql
-- List recent profiles (v3.0+)
SHOW PROFILELIST LIMIT 20;

-- Get connection ID for profile tracking
SELECT connection_id();
```

---

---

## 16. SQL Troubleshooting Toolkit

```sql
-- Query plan analysis
EXPLAIN COSTS <SQL>;
EXPLAIN VERBOSE <SQL>;

-- Query dump for offline analysis
-- wget --post-file query.sql http://<fe>:<http_port>/api/query_dump?db=<db> -O dump.json

-- Data skew check
ADMIN SHOW REPLICA DISTRIBUTION FROM <table>;

-- Unknown errors: disable features one by one
SET disable_join_reorder = true;
SET enable_global_runtime_filter = false;
SET enable_query_cache = false;
SET cbo_enable_low_cardinality_optimize = false;
```

---