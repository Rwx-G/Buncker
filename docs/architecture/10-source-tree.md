# 10. Source Tree

```
buncker/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── CODE_OF_CONDUCT.md
├── Makefile
├── pyproject.toml
├── .gitignore
├── shared/
│   ├── __init__.py
│   ├── crypto.py
│   ├── exceptions.py
│   ├── logging.py
│   ├── oci.py
│   └── wordlist.py
├── buncker/
│   ├── __init__.py
│   ├── __main__.py
│   ├── auth.py
│   ├── config.py
│   ├── handler.py
│   ├── registry_client.py
│   ├── resolver.py
│   ├── server.py
│   ├── store.py
│   └── transfer.py
├── buncker_fetch/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cache.py
│   ├── config.py
│   ├── fetcher.py
│   ├── registry_client.py
│   └── transfer.py
├── tests/
│   ├── conftest.py
│   ├── test_packaging.py
│   ├── shared/
│   │   ├── test_crypto.py
│   │   ├── test_exceptions.py
│   │   ├── test_logging.py
│   │   └── test_oci.py
│   ├── buncker/
│   │   ├── test_auth.py
│   │   ├── test_cli_ux.py
│   │   ├── test_config.py
│   │   ├── test_handler.py
│   │   ├── test_main.py
│   │   ├── test_registry_client.py
│   │   ├── test_resolver.py
│   │   ├── test_server_integration.py
│   │   ├── test_store.py
│   │   └── test_transfer.py
│   ├── buncker_fetch/
│   │   ├── test_cache.py
│   │   ├── test_cli.py
│   │   ├── test_fetcher.py
│   │   ├── test_registry_client.py
│   │   └── test_transfer.py
│   ├── e2e/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── test_api_auth_e2e.py
│   │   ├── test_error_handling.py
│   │   ├── test_full_cycle.py
│   │   └── test_key_rotation.py
│   └── integration/
│       ├── README.md
│       ├── docker-compose.yml
│       ├── outputs.md
│       ├── sample/
│       │   └── Dockerfile.simple
│       ├── client/
│       │   └── Dockerfile
│       ├── offline/
│       │   └── Dockerfile
│       ├── online/
│       │   └── Dockerfile
│       └── scripts/
│           ├── fetch-manifest.py
│           ├── inject-manifest.py
│           └── sync-salt.py
├── packaging/
│   ├── buncker/debian/
│   │   ├── conffiles
│   │   ├── control
│   │   ├── install
│   │   ├── postinst
│   │   ├── buncker.service
│   │   └── rules
│   └── buncker-fetch/debian/
│       ├── control
│       ├── install
│       └── rules
└── docs/
    ├── architecture.md
    ├── prd.md
    ├── architecture/
    │   └── *.md (sharded sections)
    ├── prd/
    │   └── *.md (sharded sections)
    └── stories/
        └── *.story.md
```

---
