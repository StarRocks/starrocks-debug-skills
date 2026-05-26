---
type: reference
category: compaction
keywords: [be_compactions, LATEST_COMPACTION_SCORE, CANDIDATE_MAX_SCORE]
---

# 02 - Compaction Information Schema Queries

---

## 9. BE_Compactions - Compaction Health

```sql
SELECT * FROM information_schema.be_compactions;
```

> **Criteria**: `LATEST_COMPACTION_SCORE` and `CANDIDATE_MAX_SCORE` < 100 indicates healthy state.

---