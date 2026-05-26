---
type: reference
category: common
keywords: [log search, grep, cluster status, SHOW BACKENDS, current_queries, be_configs, ADMIN SET]
---

# 01 - Common Diagnostic Commands

Shared reference used across all skills. Covers universal log search patterns and
cluster-wide status commands.

---

## 1. Log Search

```bash
# Search FE for a specific query
grep "<query_id>" fe.log

# Search BE for a specific instance
grep "<instance_id>" be.INFO

# Search for errors
grep -i "error\|exception\|fail" fe.WARN | tail -100

# Search for import errors by label/txn_id
grep "<label_or_txn_id>" fe.INFO
```

---

---

## 10. Cluster Status

```sql
-- View BE status
SHOW BACKENDS;

-- View currently running queries
SHOW PROC '/current_queries';

-- View import tasks
SHOW LOAD;

-- View statistics collection status
SHOW ANALYZE STATUS;

-- Dynamically modify BE parameters
UPDATE information_schema.be_configs SET value = '<new_value>' WHERE name = '<param_name>';

-- Dynamically modify FE parameters
ADMIN SET FRONTEND CONFIG("<param_name>" = "<value>");

-- Speed up tablet balancing
ADMIN SET FRONTEND CONFIG("schedule_slot_num_per_path" = "10");

-- Check data distribution across nodes
SELECT host_name() AS h, count(*) FROM <db>.<table> GROUP BY h;
```

### FE Leader Switch

```
# Manual leader switch (when needed)
# java -jar fe/lib/<je_jar> DbGroupAdmin \
#   -helperHosts <fe_master_ip>:<edit_log_port> \
#   -groupName PALO_JOURNAL_GROUP \
#   -transferMaster -force <node_name> 5000
#
# JE JAR:
#   <= 2.5  ->  je-7.3.7.jar
#   >= 3.0  ->  starrocks-bdb-je-18.3.16.jar
```

### Stream Load State

```
# When commit succeeded but data is not yet visible (high-throughput cluster):
# curl -s --location-trusted -u root: \
#   http://<fe_ip>:<fe_port>/api/<db>/get_load_state?label=<label>
```

---