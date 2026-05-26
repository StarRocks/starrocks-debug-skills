---
type: reference
category: import
issue: orc-compression-buffer-overflow
keywords: [file, export, import, orc, hdfs, large-table, compression, snappy, zstd, target_max_file_size]
---

# Case-018: ORC Compression Buffer Overflow in Large Data Exports

## Environment

- StarRocks version: 3.5.15
- Architecture: shared-nothing

## Symptom

Customer reports file export/import behavior differs by table size:

- **Small tables**: file export to HDFS → file import works normally
- **Large table (257GB)**: file export to HDFS succeeds, but file import fails consistently

User verified:
- ORC file is complete (checked with HDFS and Hive commands)
- Querying first few hundred rows from the ORC file works

**Error (lz4 compression):**
```
ORC compression buffer overflow with default compression
```

**Error (snappy compression):**
```
Compressed data exceeds reserved buffer size
```

## Investigation

### Step 1: Verify ORC File Integrity

User checked ORC file with HDFS and Hive commands - file appears complete.

```bash
hdfs dfs -cat /path/to/orc/file.orc | hive --orcfiledump
```

### Step 2: Query ORC File

Querying first few hundred rows from the ORC file works normally.

### Step 3: Test Compression Alternatives

| Compression | File Size | Result |
|---|---|---|
| lz4 (default) | Default 1GB | ❌ Import fails |
| snappy | Default 1GB | ❌ Buffer overflow error |
| snappy | 64MB (`target_max_file_size=67108864`) | ✅ Import succeeds |
| zstd | Default 1GB | ✅ Import succeeds |

### Step 4: Verify Solutions

Tested twice: snappy (with reduced file size) and zstd both work reliably.

## Root Cause

**ORC compression implementation issue in large data scenarios:**

1. **Buffer overflow**: ORC's own compression implementation has issues when handling large data volumes
2. **Snappy compression**: Compressed data exceeds reserved buffer size
3. **Default file size**: `target_max_file_size` defaults to 1GB, which is too large for some scenarios
4. **Data characteristics**: Low compression ratio combined with large total data size makes this issue more likely to occur

## Resolution

### Short-term (Workarounds)

**Option 1: Use zstd compression (Recommended)**

```sql
-- More stable and reliable for large data exports
EXPORT DATA SELECT * FROM large_table 
TO 'hdfs://path/file.orc' 
PROPERTIES ('compression' = 'zstd');
```

**Option 2: Reduce file size with snappy**

```sql
-- Set target_max_file_size to 64MB
EXPORT DATA SELECT * FROM large_table 
TO 'hdfs://path/file.orc' 
PROPERTIES (
  'compression' = 'snappy',
  'target_max_file_size' = '67108864'
);
```

### Long-term

ORC compression implementation needs optimization for large data scenarios:
- Dynamic buffer sizing based on data characteristics
- Better handling of low-compression-ratio data
- Adaptive file size recommendations

## Key Commands

```bash
# Verify ORC file integrity
hdfs dfs -cat /path/to/orc/file.orc | hive --orcfiledump

# Check ORC file content
SELECT * FROM hive_table LIMIT 100;

# Export with zstd compression
EXPORT DATA SELECT * FROM table TO 'hdfs://path/file.orc' 
PROPERTIES ('compression' = 'zstd');

# Export with snappy + reduced file size
EXPORT DATA SELECT * FROM table TO 'hdfs://path/file.orc' 
PROPERTIES (
  'compression' = 'snappy',
  'target_max_file_size' = '67108864'
);

# Import ORC file
LOAD LABEL load_label
(
  DATA INFILE('hdfs://path/file.orc')
  INTO TABLE target_table
  FORMAT AS 'ORC'
);
```

## Lessons Learned

1. **ORC compression in large data**: ORC compression may have compatibility issues in large data scenarios
2. **target_max_file_size**: Default 1GB may not fit all scenarios - adjust based on data characteristics
3. **zstd is more stable**: zstd compression is more reliable than lz4/snappy for large data exports
4. **Low compression ratio combined with large data size**: This combination easily triggers buffer overflow issues

---

## Related Skills

- [02-import.md](../../[SKILL.md](../SKILL.md)) — Import troubleshooting