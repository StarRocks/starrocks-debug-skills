---
type: reference
category: deployment
keywords: [checkpoint, BDB, FE heartbeat, tablet repair delay, reach limit of connections,
           tcmalloc large alloc, tablet migrate failed, too many open files, lock file]
---

# 02 - Deployment Diagnostics: Checkpoint, Operational Issues & Heartbeat

---

## 11. Checkpoint Troubleshooting

```bash
# Check checkpoint status
grep "checkpoint" fe.log | tail -20

# If checkpoint fails and the image is too old, check:
#   1. Disk space on FE metadata directory
#   2. FE logs for checkpoint exceptions
#   3. BDB-JE log cleaner status
```

---

---

## 13. Common Operational Issues

| Issue | Solution |
|---|---|
| `reach limit of connections` | `ALTER USER 'x' SET PROPERTIES ('max_user_connections'='1000');` Check load balancers; reduce `wait_timeout`. |
| `tcmalloc: large alloc` in BE log | Large memory allocation; find `query_id` in `be.INFO` to locate SQL. |
| Tablet scheduling slow on new nodes | `ADMIN SET FRONTEND CONFIG("schedule_slot_num_per_path"="8");` and `ADMIN SET FRONTEND CONFIG("max_scheduling_tablets"="1000");`. |
| `Fail to get master client from cache` | FE-BE communication failure; check IP/port connectivity. |
| `tablet migrate failed` | Check `storage_medium` mismatch: `ALTER TABLE db.tbl MODIFY PARTITION (*) SET("storage_medium"="HDD");`. |
| High-concurrency slowdown | Set BE parameter `brpc_connection_type = pooled`; restart BE. |
| `too many open files` | Check `cat /proc/$pid/limits`; increase fd limits. |
| BE fails to start with "lock file" error | Previous process still running; kill daemon and restart. |

---

---

## 15. FE-BE Heartbeat

FE sends heartbeat to BE every 5 seconds (`Config.heartbeat_timeout_second`). If 3
consecutive heartbeats fail (`Config.heartbeat_retry_times`), BE is marked `not alive`.
After a 60-second delay (`Config.tablet_repair_delay_factor_second`), tablet replication
begins. If the BE recovers, its replicas are deleted.

---