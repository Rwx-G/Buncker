# 8. REST API Spec

## OCI Distribution API (pull subset)

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/v2/` | Version check | None |
| GET | `/v2/{name}/manifests/{reference}` | Fetch manifest | None |
| HEAD | `/v2/{name}/manifests/{reference}` | Check manifest existence | None |
| GET | `/v2/{name}/blobs/{digest}` | Fetch blob | None |
| HEAD | `/v2/{name}/blobs/{digest}` | Check blob existence | None |

Required headers on responses: `Docker-Content-Digest`, `Content-Type`, `Content-Length`.

OCI endpoints are **always unauthenticated** regardless of auth configuration.

## Admin API

| Method | Path | Purpose | Auth (V2) |
|--------|------|---------|-----------|
| POST | `/admin/analyze` | Analyze Dockerfile(s) - accepts `dockerfile_path` (localhost) or `dockerfile_content` (remote) | Admin |
| POST | `/admin/generate-manifest` | Generate request.json.enc - returns file in response body | Admin |
| POST | `/admin/import` | Import response.tar.enc (local CLI, multipart/form-data) | Admin |
| PUT | `/admin/import` | Streaming upload of response.tar.enc (remote, `curl -T`) | Admin |
| GET | `/admin/status` | Store state | Read-only |
| GET | `/admin/gc/report` | GC candidates report | Read-only |
| POST | `/admin/gc/execute` | Execute GC (requires operator + digests) | Admin |
| GET | `/admin/logs` | Query logs (filter by event, since, limit) | Read-only |

### Authentication (V2 - after `buncker api-setup`)

When `api.enabled: true` in config, all `/admin/*` requests require `Authorization: Bearer <token>`.

| Response | Condition |
|----------|-----------|
| 401 Unauthorized | Missing or invalid token |
| 403 Forbidden | Valid read-only token on an admin-only endpoint |

When `api.enabled: false` (default, no `api-setup` run), all endpoints behave as V1 (no auth).

### PUT /admin/import - Streaming Upload

| Header | Required | Purpose |
|--------|----------|---------|
| `Authorization: Bearer <token>` | Yes (when auth enabled) | Admin token |
| `X-Buncker-Checksum: sha256:<hex>` | Yes | Pre-decryption integrity check |
| `Content-Range: bytes <start>-<end>/<total>` | No | Resume partial upload |
| `Content-Length` | Yes | Total body size |

The daemon writes the body to disk in chunks (never loads entirely in memory). After upload, it verifies the checksum before running the standard import pipeline (decrypt, HMAC, SHA256 per blob, atomic store).

Resume: `curl -C - -T response.tar.enc -H "X-Buncker-Checksum: sha256:..." https://buncker:5000/admin/import`

### Audit Log Fields (V2)

All API requests are logged with additional fields:

| Field | Values |
|-------|--------|
| `client_ip` | Source IP address |
| `auth_level` | `admin`, `readonly`, `local`, `rejected` |
| `user_agent` | User-Agent header value |

---
