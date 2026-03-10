# Integration Test Environment - Air-Gapped Docker Simulation

Multi-container environment simulating the real air-gapped workflow:
an **online** machine (buncker-fetch), an **offline** server (buncker),
and an **offline client** (docker build).

## Architecture

```
                    INTERNET
                       |
               [online-net bridge]
                       |
                  +---------+
                  |  online  |  buncker-fetch .deb
                  |  :2201   |  fetches from Docker Hub
                  +---------+
                       |
                  /transfer/ (shared volume = USB key)
                       |
                  +---------+
                  |  client  |  Docker daemon + CLI
                  |  :2203   |  builds Dockerfiles
                  +---------+
                       |
              [offline-net internal]
                       |
                  +---------+
                  | buncker  |  buncker .deb + daemon
                  |  :2202   |  OCI registry on :5000
                  +---------+
```

**Networks:**
- `online-net` - bridge (internet access)
- `offline-net` - internal only (no internet)

**SSH credentials for all containers:** `root` / `buncker`

## Prerequisites

- Docker Engine with Compose v2
- Pre-built .deb packages in `dist/` (run `make build-deb` from project root)

## Step 0: Build and launch

```bash
# From project root
make build-deb
cd tests/integration
docker compose up -d --build
```

Verify all containers are running:

```bash
docker compose ps
```

## Step 1: Setup buncker (offline server)

SSH into buncker-offline:

```bash
ssh root@localhost -p 2202
# password: buncker
```

Initialize buncker:

```bash
buncker setup
```

Expected output:
```
[1/4] Generating cryptographic keys...  done
[2/4] Initializing store...             done
[3/4] Saving configuration...           done
[4/4] Enabling and starting daemon...   done

============================================================

  IMPORTANT - Write down your 16-word recovery mnemonic.
  This is the ONLY time it will be displayed.

  <8 words>
  <8 words>

  Config:  /etc/buncker/config.json
  Store:   /var/lib/buncker
  Daemon:  active on 127.0.0.1:5000

============================================================
```

**Save the 16 words** - they are needed for pairing.

Setup auto-starts the daemon via systemd and stores the mnemonic in
`/etc/buncker/env`. In Docker containers (no systemd), step 4 shows
"skipped" and you must start the daemon manually:

```bash
BUNCKER_MNEMONIC="<16 words>" buncker serve
```

Expected output:
```
Buncker serving on 127.0.0.1:5000
```

Leave this running. Open new terminals for next steps.

## Step 3: Pair buncker-fetch (online machine)

SSH into online:

```bash
ssh root@localhost -p 2201
# password: buncker
```

Pair with the offline server:

```bash
buncker-fetch pair
# Enter the same 16-word mnemonic
```

Expected output:
```
Pairing successful - shared key derived
```

## Step 4: Analyze Dockerfile (offline server)

Open a new SSH session to buncker-offline:

```bash
ssh root@localhost -p 2202
```

Analyze a sample Dockerfile:

```bash
# The sample Dockerfile is on the client container.
# In a real scenario, the operator would have the Dockerfile on the offline machine.
# For testing, create a simple one:
echo 'FROM python:3.11-slim' > /tmp/test.Dockerfile
buncker analyze /tmp/test.Dockerfile
```

Expected output:
```json
{
  "images": ["python:3.11-slim"],
  "missing_blobs": 5,
  "total_size": "45.2 MB"
}
```

## Step 5: Generate manifest (offline server)

On buncker-offline:

```bash
buncker generate-manifest
```

Expected output:
```
Transfer request written to /var/lib/buncker/requests/request.json.enc
```

Copy the request file to a location the client can reach:

```bash
cp /var/lib/buncker/requests/request.json.enc /tmp/request.json.enc
```

## Step 6: Transfer request to online (via client)

In a real air-gapped setup, you would copy the file to a USB key.
Here, the `/transfer` volume simulates the USB key between `online` and `client`.

Since buncker-offline and client share the `offline-net` network,
you can copy the file via SSH or use docker cp:

```bash
# From the host
docker compose cp buncker-offline:/tmp/request.json.enc ./request.json.enc
docker compose cp ./request.json.enc client:/transfer/request.json.enc
```

Then from the client container, the file is at `/transfer/request.json.enc`,
which is also visible from the online container at the same path.

## Step 7: Fetch blobs (online machine)

SSH into online:

```bash
ssh root@localhost -p 2201
```

```bash
buncker-fetch fetch /transfer/request.json.enc --output /transfer/
```

Expected output:
```
Fetching blob 1/5: sha256:abc123... (12.3 MB)
Fetching blob 2/5: sha256:def456... (8.1 MB)
...
Response written to /transfer/response.tar.enc
```

## Step 8: Transfer response back to offline

The response file is already on the shared `/transfer` volume.
Use docker cp to move it to buncker-offline:

```bash
# From the host
docker compose cp client:/transfer/response.tar.enc ./response.tar.enc
docker compose cp ./response.tar.enc buncker-offline:/tmp/response.tar.enc
```

## Step 9: Import blobs (offline server)

SSH into buncker-offline:

```bash
ssh root@localhost -p 2202
```

```bash
buncker import /tmp/response.tar.enc
```

Expected output:
```
Import complete: 5 blobs, 45.2 MB
```

## Step 10: Docker build (client)

SSH into client:

```bash
ssh root@localhost -p 2203
# password: buncker
```

Build using the buncker registry as source:

```bash
docker build -t test-app /workspace/
```

Docker will pull `python:3.11-slim` from `buncker-offline:5000` (configured
as insecure registry) and build the image.

Expected output:
```
Step 1/5 : FROM python:3.11-slim
3.11-slim: Pulling from library/python
... pulled from buncker-offline:5000 ...
Successfully built abc123
Successfully tagged test-app:latest
```

## Step 11: Verify status (offline server)

```bash
ssh root@localhost -p 2202
buncker status
```

Expected output:
```
Buncker registry status:
  Blobs: 5
  Total size: 45.2 MB
  Listening on: 127.0.0.1:5000
```

## Automated Test Script

An automated 3-phase test script runs the full integration suite:

```bash
# From project root
make integration-test
# Or directly:
bash tests/integration/scripts/test-full-flow.sh
```

The script executes 40 checks across 3 phases:

| Phase | Description | Checks |
|-------|-------------|--------|
| **Phase 1** | Core flow: setup, pair, analyze, generate-manifest, fetch, import, docker build | ~20 |
| **Phase 2** | API auth + LAN client: api-setup, token auth, TLS, curl-based admin operations | ~11 |
| **Phase 3** | OCI restricted mode: `--restrict-oci`, 401 challenges, Bearer token on `/v2/*` | ~9 |

Phase 3 validates that:
- `/v2/` without token returns 401 with `WWW-Authenticate: Bearer` header
- `/v2/` with readonly or admin token returns 200
- Manifest and blob pulls without token return 401
- HEAD requests without token return 401
- Admin `/status` endpoint remains accessible
- Invalid tokens are rejected

## Verification checklist

- [ ] `docker compose up -d --build` starts all 3 containers
- [ ] SSH into each container works (ports 2201, 2202, 2203)
- [ ] buncker-offline cannot reach internet (`curl google.com` fails)
- [ ] client cannot reach internet
- [ ] online can reach Docker Hub (`curl https://registry-1.docker.io/v2/`)
- [ ] Full cycle: setup -> pair -> analyze -> generate-manifest -> fetch -> import -> docker build

## Teardown

```bash
docker compose down -v
```
