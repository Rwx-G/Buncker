# 7. Core Workflows

## Workflow 1 - Dockerfile Analysis + Request Generation

```mermaid
sequenceDiagram
    participant OP as Operator
    participant D as buncker daemon
    participant RES as resolver
    participant RC as registry_client (cache)
    participant ST as store
    participant CR as crypto

    OP->>D: POST /admin/analyze {dockerfile, build_args}
    D->>RES: parse_dockerfile(path, build_args)
    RES->>RES: Parse pre-FROM ARGs + substitution
    RES->>RES: Extract FROM lines, detect internal aliases
    loop For each external image
        RES->>RC: get_manifest(registry, repo, tag)
        RC->>ST: Read cached manifest
        alt Cached
            ST-->>RES: OCIManifest
        else Not cached
            RC-->>RES: None (error: unknown manifest)
        end
        RES->>ST: list_missing(layer_digests)
        ST-->>RES: missing blobs list
    end
    RES-->>D: AnalysisResult (images, missing_blobs, warnings)
    D-->>OP: Analysis report

    OP->>D: POST /admin/generate-manifest
    D->>CR: encrypt(request_json, aes_key)
    CR-->>D: request.json.enc
    D-->>OP: File ready → USB
```

## Workflow 2 - Online Fetch

```mermaid
sequenceDiagram
    participant OP as Operator
    participant CLI as buncker-fetch
    participant CR as crypto
    participant CA as cache
    participant RC as registry_client
    participant REG as Public registries

    OP->>CLI: buncker-fetch fetch request.json.enc
    CLI->>CR: decrypt + verify HMAC
    alt Invalid HMAC
        CR-->>CLI: ERROR
        CLI-->>OP: Refused, nothing downloaded
    end
    CR-->>CLI: request JSON

    loop For each blob (ThreadPoolExecutor)
        CLI->>CA: has_blob(digest)?
        alt Cached
            CA-->>CLI: skip
        else Not cached
            CLI->>RC: authenticate(registry)
            RC->>REG: Token exchange
            CLI->>RC: fetch_blob(digest)
            RC->>REG: GET blob
            REG-->>RC: blob data
            CLI->>CLI: verify SHA256
            CLI->>CA: store_blob(digest, data)
        end
    end

    CLI->>CR: sign + encrypt → response.tar.enc
    CLI-->>OP: File ready → USB
```

## Workflow 3 - Import Response (offline)

```mermaid
sequenceDiagram
    participant OP as Operator
    participant D as buncker daemon
    participant CR as crypto
    participant ST as store

    OP->>D: POST /admin/import {response.tar.enc}
    D->>CR: decrypt + verify HMAC
    alt Failed
        D-->>OP: Refused
    end
    loop For each blob
        D->>D: verify SHA256
        alt Mismatch
            D-->>OP: ERROR: corrupt blob
        else OK
            D->>ST: import_blob (atomic write)
        end
    end
    D-->>OP: Import complete: N blobs, X MB
```

## Workflow 4 - Docker Pull

```mermaid
sequenceDiagram
    participant DC as Docker client
    participant D as buncker daemon
    participant ST as store

    DC->>D: GET /v2/
    D-->>DC: 200 OK
    DC->>D: GET /v2/{name}/manifests/{ref}
    D->>ST: get_manifest
    D-->>DC: 200 manifest (or 404)
    loop For each layer
        DC->>D: GET /v2/{name}/blobs/{digest}
        D->>ST: get_blob + update_metadata
        D-->>DC: 200 blob stream
    end
```

## Workflow 5 - Setup / Pairing

```mermaid
sequenceDiagram
    participant OFF as Operator (offline)
    participant D as buncker daemon
    participant ON as Operator (online)
    participant F as buncker-fetch

    OFF->>D: buncker setup
    D->>D: generate_mnemonic() → 16 BIP-39 words (12 secret + 4 salt)
    D->>D: derive_keys + save config
    D-->>OFF: Display 16 words (write on paper)
    Note over OFF,ON: Human channel (verbal, paper)
    ON->>F: buncker-fetch pair
    F-->>ON: Enter 16 words
    ON->>F: word1 word2 ... word16
    F->>F: derive_keys + save config
    F-->>ON: Pairing OK
```

## Workflow 6 - API Setup (V2)

```mermaid
sequenceDiagram
    participant OP as Operator (local)
    participant D as buncker daemon

    OP->>D: buncker api-setup [--cert cert.pem --key key.pem]
    D->>D: Generate read-only token (256-bit)
    D->>D: Generate admin token (256-bit)
    alt Certificate provided
        D->>D: Configure TLS with provided cert
    else No certificate
        D->>D: Generate auto-signed cert + CA
        D-->>OP: WARNING: auto-signed certificate
    end
    D->>D: Save tokens to /etc/buncker/api-tokens.json (0600)
    D->>D: Update config: api.enabled=true, tls=true
    D-->>OP: Display read-only token
    D-->>OP: Display admin token
    Note over OP: Distribute tokens to LAN clients
```

## Workflow 7 - Remote Analysis via curl (V2)

```mermaid
sequenceDiagram
    participant LC as LAN Client (curl)
    participant D as buncker daemon
    participant AUTH as Auth Middleware
    participant RES as resolver
    participant ST as store

    LC->>D: POST /admin/analyze {dockerfile_content, build_args}
    D->>AUTH: Validate Bearer token
    alt Invalid/missing token
        AUTH-->>LC: 401 Unauthorized
    else Read-only token
        AUTH-->>LC: 403 Forbidden
    else Admin token
        AUTH-->>D: OK
    end
    D->>RES: parse_dockerfile(content, build_args)
    RES-->>D: AnalysisResult
    D-->>LC: JSON analysis report

    LC->>D: POST /admin/generate-manifest
    D->>AUTH: Validate Bearer token (admin)
    D-->>LC: request.json.enc (application/octet-stream)
```

## Workflow 8 - Remote Streaming Import via curl (V2)

```mermaid
sequenceDiagram
    participant LC as LAN Client (curl)
    participant D as buncker daemon
    participant AUTH as Auth Middleware
    participant CR as crypto
    participant ST as store

    LC->>D: PUT /admin/import (streaming body)
    Note over LC,D: Headers: Authorization, X-Buncker-Checksum, Content-Range (optional)
    D->>AUTH: Validate Bearer token (admin)
    alt Invalid token
        AUTH-->>LC: 401 Unauthorized
    end

    D->>D: Stream body to temp file (chunked)
    alt Content-Range present (resume)
        D->>D: Append to existing partial upload
    end
    D->>D: Verify X-Buncker-Checksum vs received file
    alt Checksum mismatch
        D-->>LC: 400 Upload integrity check failed
    end

    D->>CR: decrypt + verify HMAC
    loop For each blob
        D->>D: verify SHA256
        D->>ST: import_blob (atomic write)
    end
    D-->>LC: JSON import summary
```

---
