# 10. Source Tree

```
buncker/
├── README.md
├── LICENSE
├── Makefile
├── shared/
│   ├── __init__.py
│   ├── crypto.py
│   ├── oci.py
│   └── wordlist.py
├── buncker/
│   ├── __init__.py
│   ├── __main__.py
│   ├── server.py
│   ├── handler.py
│   ├── auth.py
│   ├── resolver.py
│   ├── registry_client.py
│   ├── store.py
│   ├── transfer.py
│   └── config.py
├── buncker_fetch/
│   ├── __init__.py
│   ├── __main__.py
│   ├── fetcher.py
│   ├── registry_client.py
│   ├── cache.py
│   ├── transfer.py
│   └── config.py
├── tests/
│   ├── conftest.py
│   ├── shared/
│   │   ├── test_crypto.py
│   │   └── test_oci.py
│   ├── buncker/
│   │   ├── test_resolver.py
│   │   ├── test_store.py
│   │   ├── test_registry_client.py
│   │   ├── test_handler.py
│   │   ├── test_auth.py
│   │   ├── test_transfer.py
│   │   └── test_server_integration.py
│   └── buncker_fetch/
│       ├── test_fetcher.py
│       ├── test_registry_client.py
│       ├── test_cache.py
│       └── test_transfer.py
├── packaging/
│   ├── buncker/debian/
│   │   ├── control
│   │   ├── rules
│   │   ├── install
│   │   ├── buncker.service
│   │   ├── postinst
│   │   └── conffiles
│   └── buncker-fetch/debian/
│       ├── control
│       ├── rules
│       └── install
├── pyproject.toml
└── .gitignore
```

---
