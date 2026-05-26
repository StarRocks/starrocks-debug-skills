---
type: reference
category: deployment
issue: ssl-certificate
keywords: [SSL, OSS, Broker Load, ssl_enable, private object storage]
---

# Case-005: SSL Certificate Error Blocking Broker Load (Private OSS)

## Environment

- StarRocks version: 2.5.22
- Architecture: shared-nothing

## Symptom

Broker Load from a private object-storage service failed with:

```
curlCode: 60, SSL peer certificate or SSH remote key was not OK
```

## Investigation

The cluster was accessing a private OSS-style service that did not support SSL. StarRocks
defaults to SSL-encrypted access, and the `ssl_enable` parameter could not be disabled
due to a parameter-binding bug in the affected version.

## Root Cause

Default-on SSL plus a non-functional `ssl_enable` flag prevented Broker Load from
talking to the SSL-less private storage endpoint.

## Resolution

- Upgrade to a version where the `ssl_enable` flag is honored, then disable SSL for the
  affected storage volume.

## Lessons Learned

- Always confirm whether the target object storage supports SSL before configuring Broker
  Load — defaults assume SSL is available.
- "Parameter has no effect" is a real failure mode; verify with the BE/FE log lines that
  echo the resolved value.

## Related Skills

- [SKILL.md](../SKILL.md) — SSL issues with private OSS
- [SKILL.md](../SKILL.md) — SSL errors during imports
