---
type: reference
category: shared-data
keywords: [be_datacache_metrics, be_cloud_native_compactions, DISK_QUOTA_BYTES, DISK_USED_BYTES,
           read_local_sec, read_remote_sec, PROGRESS, PROFILE, lake compaction]
---

# 02 - DataCache & Cloud-Native Compaction Schema Queries

---

## 3. BE_DataCache_Metrics - Data Cache Status

```sql
-- Data Cache memory and disk capacity (shared-data clusters)
SELECT * FROM information_schema.be_datacache_metrics;
```

| Field | Description |
|---|---|
| BE_ID | Backend ID |
| STATUS | Normal/Abnormal |
| DISK_QUOTA_BYTES | Disk quota |
| DISK_USED_BYTES | Disk used |
| MEM_QUOTA_BYTES | Memory quota |
| MEM_USED_BYTES | Memory used |
| META_USED_BYTES | Metadata memory |
| DIR_SPACES | Disk space details |

---

---

## 6. BE_Cloud_Native_Compactions - Shared-Data Compaction

> **Note**: Only available in shared-data (cloud-native) clusters.

```sql
SELECT * FROM information_schema.be_cloud_native_compactions;
```

| Field | Description |
|---|---|
| BE_ID | Backend ID |
| TXN_ID | Transaction ID |
| TABLET_ID | Tablet ID |
| VERSION | Version number |
| PROGRESS | Progress percentage |
| STATUS | Error message if any |
| PROFILE | Execution metrics (v3.2.12+, v3.3.5+) |

### Profile Fields (JSON)

| Field | Description |
|---|---|
| read_local_sec | Local cache read time (seconds) |
| read_local_mb | Local cache read size (MB) |
| read_remote_sec | Remote S3/HDFS read time (seconds) |
| read_remote_mb | Remote read size (MB) |
| read_remote_count | Remote read count |
| read_local_count | Local cache read count |
| in_queue_sec | Queue wait time (seconds) |

---