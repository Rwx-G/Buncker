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
| GET | `/admin/status` | Store state + disk usage | Read-only |
| GET | `/admin/health` | Health check (store integrity, disk, TLS cert expiry, uptime) | Read-only |
| GET | `/admin/gc/report` | GC candidates report | Read-only |
| POST | `/admin/gc/impact` | Impact analysis before deletion (affected images) | Admin |
| POST | `/admin/gc/execute` | Execute GC (requires operator + digests) | Admin |
| GET | `/admin/logs` | Query logs (filter by event, since, limit) | Read-only |

### GET /admin/status - Response

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | Buncker version |
| `source_id` | string | Instance identifier |
| `store_path` | string | OCI store directory |
| `blob_count` | int | Number of stored blobs |
| `total_size` | int | Total blob size in bytes |
| `disk_total` | int | Total disk space in bytes |
| `disk_used` | int | Used disk space in bytes |
| `disk_free` | int | Free disk space in bytes |
| `uptime` | int | Server uptime in seconds |

### GET /admin/health - Response

Returns 200 when healthy, 503 otherwise.

| Field | Type | Description |
|-------|------|-------------|
| `healthy` | bool | Overall health (store OK, disk > 100 MiB, TLS cert not expired) |
| `store.oci_layout_valid` | bool | OCI layout marker file exists |
| `store.blob_count` | int | Number of stored blobs |
| `disk.total` | int | Total disk space in bytes |
| `disk.used` | int | Used disk space in bytes |
| `disk.free` | int | Free disk space in bytes |
| `uptime` | int | Server uptime in seconds |
| `tls` | object? | Present only when TLS is configured |
| `tls.not_valid_after` | string | Certificate expiry (ISO 8601) |
| `tls.days_until_expiry` | int | Days until certificate expires |
| `tls.expired` | bool | Whether the certificate has expired |

### POST /admin/gc/impact - Request & Response

Request body: `{"digests": ["sha256:abc...", ...]}`

Response: `{"impact": [...], "affected_images": <int>}` - lists images that would become non-pullable if the specified blobs are deleted. Use this before `gc/execute` to preview consequences.

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
