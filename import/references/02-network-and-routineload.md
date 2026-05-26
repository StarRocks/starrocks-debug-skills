---
type: reference
category: import
keywords: [brpc, netstat, tcpdump, routine load, desired_concurrent_number, max_routine_load_task_concurrent_num]
---

# 02 - Network Diagnostics & Routine Load Tuning

---

## 4. Network Diagnostics

```bash
# Check brpc port connection state
netstat -na | grep 8060

# Check brpc latency metrics
curl -s http://<be_ip>:8060/vars | grep exec_

# Packet capture
tcpdump -i <interface> host <target_ip> and port 8060 -w /tmp/dump.pcap
```

---

---

## 8. Routine Load Tuning

```sql
-- Check routine load task status
SHOW ROUTINE LOAD TASK WHERE JobName = "<job_name>";
SHOW ROUTINE LOAD FOR <job_name>;

-- Check for "too many versions" (import too fast)
-- In BE log: grep "too many versions" be.INFO
-- Via SQL:
SELECT * FROM information_schema.be_tablets ORDER BY NUM_VERSION DESC LIMIT 10;
```

### Key parameters

- FE: `max_routine_load_task_num_per_be` (must be less than `routine_load_thread_pool_size`),
  `max_routine_load_task_concurrent_num` (default 5).
- BE: `max_consumer_num_per_group` (default 3), `routine_load_thread_pool_size` (default 10).
- Routine Load: `desired_concurrent_number` (default 3).

**Effective parallelism** = `min(desired_concurrent_number, kafka_partitions, max_routine_load_task_concurrent_num, be_count)`.

---