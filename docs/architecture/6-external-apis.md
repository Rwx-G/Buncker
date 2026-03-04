# 6. External APIs

All public registries follow the OCI Distribution Spec. buncker-fetch implements a single client with auth discovery:

```
1. GET /v2/ → 401 + Www-Authenticate header → parse realm/service/scope
2. GET {realm}?service={service}&scope={scope} → Bearer token
3. GET /v2/{name}/manifests/{ref} + OCI Accept headers
4. GET /v2/{name}/blobs/{digest} → binary stream
```

**Registries supported:** docker.io, ghcr.io, quay.io, gcr.io, any OCI-compliant custom registry.

**Retry policy:** 3 attempts, exponential backoff (1s, 3s, 9s).
**Timeout:** 30s connect, 120s read.
**Rate limits:** Docker Hub 100 pulls/6h anonymous, 200/6h authenticated. Mitigated by local manifest cache.

---
