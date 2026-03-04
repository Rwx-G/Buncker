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
    D->>D: generate_mnemonic() → 12 BIP-39 words
    D->>D: derive_keys + save config
    D-->>OFF: Display 12 words (write on paper)
    Note over OFF,ON: Human channel (verbal, paper)
    ON->>F: buncker-fetch pair
    F-->>ON: Enter 12 words
    ON->>F: word1 word2 ... word12
    F->>F: derive_keys + save config
    F-->>ON: Pairing OK
```

---
