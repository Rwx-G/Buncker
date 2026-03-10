# Integration Test Outputs - 2026-03-05

## buncker setup

```
[1/4] Generating cryptographic keys...  done
[2/4] Initializing store...             done
[3/4] Saving configuration...           done
[4/4] Enabling and starting daemon...   skipped
  Warning: Could not enable daemon. Start manually with: sudo systemctl enable --now buncker

============================================================

  IMPORTANT - Write down your 16-word recovery mnemonic.
  This is the ONLY time it will be displayed.

  fat trade nuclear asthma good pave salmon glass
  bonus flat drip one sign festival remove major

  Config:  /etc/buncker/config.json
  Store:   /var/lib/buncker
  Daemon:  not started (systemctl unavailable or not root)

============================================================
```

## buncker serve

```
Enter mnemonic:
```

(daemon starts listening on 127.0.0.1:5000, no further stdout)

## buncker-fetch pair

```
Enter the 16-word mnemonic (space-separated):
>   status: success
  message: Pairing successful
```

## buncker analyze

```json
{
  "source_path": "/tmp/test.Dockerfile",
  "images": [
    {
      "raw": "alpine:3.19",
      "resolved": "docker.io/library/alpine:3.19",
      "registry": "docker.io",
      "repository": "library/alpine",
      "tag": "3.19",
      "digest": null,
      "platform": null,
      "is_internal": false,
      "is_private": false
    }
  ],
  "present_blobs": [],
  "missing_blobs": [
    {
      "registry": "docker.io",
      "repository": "library/alpine",
      "digest": "sha256:83b2b6703a620bf2e001ab57f7adc414d891787b3c59859b1b62909e48dd2242",
      "size": 581,
      "media_type": "application/vnd.oci.image.config.v1+json"
    },
    {
      "registry": "docker.io",
      "repository": "library/alpine",
      "digest": "sha256:17a39c0ba978cc27001e9c56a480f98106e1ab74bd56eb302f9fd4cf758ea43f",
      "size": 3419815,
      "media_type": "application/vnd.oci.image.layer.v1.tar+gzip"
    }
  ],
  "total_missing_size": 3420396,
  "warnings": []
}
```

## buncker generate-manifest

```
Transfer request saved to buncker-request.json.enc
```

## buncker-fetch fetch

```json
{"event": "fetch_progress", "current": 1, "total": 2, "digest": "sha256:83b2b6703a620bf2e001ab57f7adc414d891787b3c59859b1b62909e48dd2242", "skipped": false}
{"event": "fetch_progress", "current": 2, "total": 2, "digest": "sha256:17a39c0ba978cc27001e9c56a480f98106e1ab74bd56eb302f9fd4cf758ea43f", "skipped": false}
{
  "status": "success",
  "downloaded": 2,
  "skipped": 0,
  "errors": 0,
  "response_file": "/transfer/buncker-response-20260305T083643Z-buncker-1ac37e30.tar.enc"
}
```

## buncker import

```json
{
  "imported": 2,
  "skipped": 0,
  "errors": []
}
```

## buncker status

```json
{
  "version": "0.8.0",
  "source_id": "buncker-1ac37e30",
  "store_path": "/var/lib/buncker",
  "blob_count": 2,
  "total_size": 3420396,
  "uptime": 239
}
```

## docker build

```
Sending build context to Docker daemon  2.048kB
Step 1/2 : FROM buncker-offline:5000/library/alpine:3.19
3.19: Pulling from library/alpine
17a39c0ba978: Pulling fs layer
17a39c0ba978: Download complete
17a39c0ba978: Pull complete
Digest: sha256:9ba18b08ce5044c31568542647c0db07dbed0705f4699cc1b3e8ead3c2deb90e
Status: Downloaded newer image for buncker-offline:5000/library/alpine:3.19
 ---> 83b2b6703a62
Step 2/2 : CMD ["echo", "hello from buncker"]
 ---> Running in df487d32cbdf
Removing intermediate container df487d32cbdf
 ---> 591c56fe6d08
Successfully built 591c56fe6d08
Successfully tagged test-app:latest
```

## docker run test-app

```
hello from buncker
```
