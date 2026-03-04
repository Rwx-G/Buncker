# 8. REST API Spec

## OCI Distribution API (pull subset)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v2/` | Version check |
| GET | `/v2/{name}/manifests/{reference}` | Fetch manifest |
| HEAD | `/v2/{name}/manifests/{reference}` | Check manifest existence |
| GET | `/v2/{name}/blobs/{digest}` | Fetch blob |
| HEAD | `/v2/{name}/blobs/{digest}` | Check blob existence |

Required headers on responses: `Docker-Content-Digest`, `Content-Type`, `Content-Length`.

## Admin API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/admin/analyze` | Analyze Dockerfile(s) |
| POST | `/admin/generate-manifest` | Generate request.json.enc |
| POST | `/admin/import` | Import response.tar.enc (multipart/form-data) |
| GET | `/admin/status` | Store state |
| GET | `/admin/gc/report` | GC candidates report |
| POST | `/admin/gc/execute` | Execute GC (requires operator + digests) |
| GET | `/admin/logs` | Query logs (filter by event, since, limit) |

No authentication in V1 (isolated LAN). V2: optional Bearer token on admin endpoints.

---
