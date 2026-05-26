#!/usr/bin/env python3
"""
analyze_logs.py — Analyze StarRocks audit logs to find top resource consumers.

Usage:
    python3 analyze_logs.py <start_time> <end_time> <sort_field> <top_n> <log_file>

Arguments:
    start_time  — Start timestamp (e.g., "2025-04-15 00:00:00")
    end_time    — End timestamp (e.g., "2025-04-15 01:00:00")
    sort_field  — Field to sort by:
                  - MemCostBytes: BE memory cost
                  - QueryFEAllocatedMemory: FE memory cost
                  - CpuCostNs: CPU cost in nanoseconds
                  - ScanBytes: Data scan bytes
    top_n       — Number of top queries to return
    log_file    — Audit log file path (e.g., fe.audit.log)

Example:
    # Top 3 BE memory consumers
    python3 analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "MemCostBytes" 3 fe.audit.log

    # Top 3 FE memory consumers
    python3 analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "QueryFEAllocatedMemory" 3 fe.audit.log

    # Top 3 CPU-intensive queries
    python3 analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "CpuCostNs" 3 fe.audit.log

    # Top 3 scan-heavy queries
    python3 analyze_logs.py "2025-04-15 00:00:00" "2025-04-15 01:00:00" "ScanBytes" 3 fe.audit.log
"""

import sys
import re
from datetime import datetime
from collections import defaultdict


def parse_audit_line(line):
    """Parse a single audit log line and extract fields."""
    # Audit log format: [timestamp] [query_id] [user] [...] [field=value] ...
    fields = {}

    # Extract timestamp
    ts_match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
    if ts_match:
        fields['timestamp'] = ts_match.group(1)

    # Extract query_id
    qid_match = re.search(r'\[([a-f0-9-]{36})\]', line)
    if qid_match:
        fields['query_id'] = qid_match.group(1)

    # Extract key metrics from field=value pairs
    for field in ['MemCostBytes', 'QueryFEAllocatedMemory', 'CpuCostNs', 'ScanBytes', 'State', 'QueryTime']:
        match = re.search(rf'{field}=([^\s,\]]+)', line)
        if match:
            try:
                fields[field] = int(match.group(1))
            except ValueError:
                fields[field] = match.group(1)

    return fields


def main():
    if len(sys.argv) != 6:
        print(__doc__)
        sys.exit(1)

    start_time = sys.argv[1]
    end_time = sys.argv[2]
    sort_field = sys.argv[3]
    top_n = int(sys.argv[4])
    log_file = sys.argv[5]

    start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    end_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")

    queries = defaultdict(dict)

    with open(log_file, 'r') as f:
        for line in f:
            fields = parse_audit_line(line)
            if 'timestamp' not in fields:
                continue

            ts = datetime.strptime(fields['timestamp'], "%Y-%m-%d %H:%M:%S")
            if ts < start_dt or ts > end_dt:
                continue

            query_id = fields.get('query_id')
            if query_id:
                queries[query_id].update(fields)

    # Filter queries with the sort field
    valid_queries = {qid: f for qid, f in queries.items() if sort_field in f}

    # Sort by the specified field
    sorted_queries = sorted(
        valid_queries.items(),
        key=lambda x: x[1].get(sort_field, 0),
        reverse=True
    )

    # Output top N
    print(f"Top {top_n} queries by {sort_field} from {start_time} to {end_time}")
    print("-" * 80)

    for i, (qid, f) in enumerate(sorted_queries[:top_n], 1):
        print(f"{i}. Query ID: {qid}")
        print(f"   {sort_field}: {f.get(sort_field, 'N/A')}")
        print(f"   State: {f.get('State', 'N/A')}")
        print(f"   QueryTime: {f.get('QueryTime', 'N/A')} ms")
        if 'MemCostBytes' in f:
            print(f"   MemCostBytes: {f['MemCostBytes']}")
        if 'ScanBytes' in f:
            print(f"   ScanBytes: {f['ScanBytes']}")
        print()


if __name__ == '__main__':
    main()