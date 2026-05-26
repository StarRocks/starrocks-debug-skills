---
type: reference
category: data-lake
issue: kerberos-authentication
keywords: [Kerberos, keytab, GSS, Hive, HDFS, authentication, kinit, KDC]
---

# Case-016: Kerberos Authentication Failures

## Environment

- StarRocks version: Any version with Hive/HDFS external catalog
- Hadoop ecosystem with Kerberos security enabled

## Symptom

Various Kerberos-related errors when accessing Hive catalogs or HDFS external tables:

- `GSS initiate failed`
- `Server not found in Kerberos database`
- `Clock skew too great`
- `Preauthentication failed`
- `set_ugi() not successful`

## Investigation

### 1. Enable Kerberos Debug Logging

**FE side** - Add to `fe.conf`:
```
JAVA_OPTS="-Dsun.security.krb5.debug=true"
```

**BE/CN side** - Add to `be/conf/hadoop_env.sh`:
```
HADOOP_OPTS="$HADOOP_OPTS -Dsun.security.krb5.debug=true"
```

Debug output appears in `fe.out` and `be.out`.

### 2. Verify Keytab and Principal

```bash
# List principals in keytab
klist -kt /path/to/keytab

# Test kinit with keytab
kinit -kt /path/to/keytab principal_name@REALM

# View current ticket cache
klist

# Destroy ticket cache
kdestroy
```

### 3. Check Kerberos Configuration

Verify `/etc/krb5.conf`:
- `kdc` and `admin_server` point to correct KDC
- `default_realm` matches your environment
- Domain-to-realm mappings are correct

### 4. Common Error Patterns

| Error | Cause | Solution |
|---|---|---|
| `GSS initiate failed` | Expired keytab or missing JCE | Renew keytab; install JCE extensions for AES-256 |
| `Server not found in Kerberos database` | SPN mismatch | Verify service principal matches HMS/HDFS server |
| `Clock skew too great` | Time sync issue | Ensure NTP is running; check server time |
| `Preauthentication failed` | Wrong password or keytab | Verify keytab matches principal password |
| `Checksum failed` | Encryption type mismatch | Check `default_tkt_enctypes` in krb5.conf |
| `set_ugi() not successful` | HADOOP_USER_NAME issue | Configure `HADOOP_USER_NAME` in `hadoop_env.sh` |

### 5. JCE Extensions

For AES-256 encryption, Java Cryptography Extension (JCE) must be installed:

```bash
# Check if JCE is installed (Java 8)
ls $JAVA_HOME/jre/lib/security/policy/unlimited/

# For Java 9+, unlimited crypto is enabled by default
```

### 6. Keytab Rotation and Expiry

```bash
# Check keytab expiry (if using password-derived keytab)
# Keytabs don't expire themselves, but the password in AD/KDC does

# Verify principal still exists in KDC
kadmin -q "getprinc principal_name"
```

## Root Cause Analysis

Most Kerberos failures stem from:

1. **Expired credentials** - Keytab passwords rotate in AD/KDC but keytab not updated
2. **Clock drift** - Kerberos requires < 5 minutes time difference
3. **SPN mismatch** - Service Principal Name doesn't match the actual service
4. **Encryption mismatch** - Client/server encryption type incompatibility
5. **Config errors** - Wrong realm, KDC address, or domain mappings

## Resolution Steps

1. **Verify time synchronization**:
   ```bash
   ntpq -p
   timedatectl status
   ```

2. **Refresh keytab** if password rotated:
   ```bash
   # Generate new keytab
   ktutil
   ktutil: addent -password -p principal@REALM -k 1 -e aes256-cts-hmac-sha1-96
   # Enter password
   ktutil: wkt /path/to/new.keytab
   ktutil: quit
   ```

3. **Restart StarRocks FE/BE** after keytab update to pick up new credentials.

4. **Verify HMS/HDFS connectivity** with kinit before configuring catalog:
   ```bash
   kinit -kt keytab principal@REALM
   # Test HDFS access
   hdfs dfs -ls /path
   ```

## Best Practices

- Set up monitoring for keytab expiry (track password rotation in AD/KDC)
- Use a dedicated service account for StarRocks with long password lifetime
- Keep krb5.conf synchronized across FE and all BE/CN nodes
- Document the keytab rotation process and update schedule

## Related Skills

- [SKILL.md](../SKILL.md) - Hive Metastore connection issues and Kerberos debugging
- [SKILL.md](../SKILL.md) - Shared-data external catalog configuration

## Resources

- [Kerberos Debug Logging](https://docs.oracle.com/javase/8/docs/technotes/guides/security/jgss/tutorials/Debug.html)
- [StarRocks Hive Catalog with Kerberos](https://docs.starrocks.io/docs/data_source/catalog/hive_catalog/)