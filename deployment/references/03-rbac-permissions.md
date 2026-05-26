---
type: reference
category: deployment
keywords: [RBAC, grants_to_roles, grants_to_users, role_edges, permissions, sys schema,
           SHOW GRANTS, is_role_in_session]
---

# 03 - RBAC & Permission Schema Queries

---

## 18. Sys Schema - RBAC & Permissions

> **Note**: Must `USE sys;` first. Only users with `user_admin` role can query these views.

### 18.1 Grants_to_Roles

```sql
USE sys;
SELECT * FROM grants_to_roles WHERE OBJECT_DATABASE = 'rbac_test';
```

### 18.2 Grants_to_Users

```sql
USE sys;
SELECT * FROM grants_to_users WHERE OBJECT_DATABASE = 'rbac_test' AND GRANTEE LIKE '%user%';
```

### 18.3 Role_Edges

```sql
USE sys;
SELECT * FROM role_edges WHERE FROM_ROLE LIKE '%role%';
```

### 18.4 User Direct Permissions

```sql
USE sys;
SELECT * FROM grants_to_users WHERE GRANTEE = "'user_g'@'%'";
```

### 18.5 User Inherited Permissions via Roles

```sql
USE sys;
-- Direct role grants to user
SELECT DISTINCT
    gr.GRANTEE AS RoleOrUser,
    gr.OBJECT_DATABASE AS ObjectDatabase,
    gr.OBJECT_NAME AS ObjectName,
    gr.PRIVILEGE_TYPE AS PrivilegeType
FROM grants_to_roles gr
JOIN role_edges re1 ON gr.GRANTEE = re1.FROM_ROLE
WHERE re1.TO_USER = "'user_g'@'%'"
UNION
-- Level 1 inheritance
SELECT DISTINCT gr.GRANTEE, gr.OBJECT_DATABASE, gr.OBJECT_NAME, gr.PRIVILEGE_TYPE
FROM grants_to_roles gr
JOIN role_edges re1 ON gr.GRANTEE = re1.FROM_ROLE
JOIN role_edges re2 ON re1.TO_ROLE = re2.FROM_ROLE
WHERE re2.TO_USER = "'user_g'@'%'"
UNION
-- Level 2 inheritance
SELECT DISTINCT gr.GRANTEE, gr.OBJECT_DATABASE, gr.OBJECT_NAME, gr.PRIVILEGE_TYPE
FROM grants_to_roles gr
JOIN role_edges re1 ON gr.GRANTEE = re1.FROM_ROLE
JOIN role_edges re2 ON re1.TO_ROLE = re2.FROM_ROLE
JOIN role_edges re3 ON re2.TO_ROLE = re3.FROM_ROLE
WHERE re3.TO_USER = "'user_g'@'%'";
```

### 18.6 Role Inheritance Chain

```sql
USE sys;
SELECT re1.FROM_ROLE AS ParentRole, re1.TO_ROLE AS ChildRole
FROM role_edges re1
WHERE re1.FROM_ROLE = 'role_s' AND re1.TO_ROLE IS NOT NULL
UNION ALL
SELECT re2.FROM_ROLE AS ParentRole, re2.TO_ROLE AS ChildRole
FROM role_edges re1
JOIN role_edges re2 ON re1.TO_ROLE = re2.FROM_ROLE
WHERE re1.FROM_ROLE = 'role_s' AND re2.TO_ROLE IS NOT NULL;
```

### 18.7 Check Role Activation

```sql
-- Check if role is active in current session (v3.1.4+)
SELECT is_role_in_session("r1");
```

### 18.8 Simplified Permission Queries

```sql
-- Recommended: Use SHOW GRANTS instead of complex joins
SHOW GRANTS;                              -- Current user
SHOW GRANTS FOR ROLE <role_name>;         -- Specific role
SHOW GRANTS FOR <user_identity>;          -- Specific user
```

---