---
type: reference
category: query
keywords: [statistics, bitmap index, bloom filter, table_statistic_v1, index suitability]
---

# 03 - Statistics & Index Suitability Queries

---

## 17. Statistics - Index Suitability Analysis

```sql
-- Check statistics table
SELECT * FROM _statistics_.table_statistic_v1;
```

### Index Suitability Criteria

| Index Type | Suitable Condition |
|---|---|
| BITMAP | `distinct_count / row_count < 80%` AND `distinct_count` between 100-100,000 |
| BLOOM FILTER | `distinct_count / row_count > 80%` |

---