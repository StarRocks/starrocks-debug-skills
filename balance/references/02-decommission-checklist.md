---
type: reference
category: balance
keywords: [decommission, colocate, single replica, schema change, import pressure, balance bugs]
---

# 02 - BE Decommission Checklist

---

## 14. BE Decommission Checklist

Before decommissioning a BE node, verify:

1. No colocate tables affected.
2. No single-replica tables.
3. No unhealthy replicas in the cluster.
4. Cluster IO pressure is manageable.
5. No ongoing schema change operations.
6. Import pressure is acceptable.
7. No known balance bugs.

---