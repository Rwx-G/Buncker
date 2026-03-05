"""Sync buncker-fetch config to use the same salt as buncker daemon.

In production, the pair command handles salt exchange.
In this test environment (separate containers), we manually sync the salt.
"""

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, "/usr/lib/buncker-fetch")
from shared.crypto import derive_keys, encrypt  # noqa: E402

DAEMON_SALT = sys.argv[1]  # base64-encoded salt from buncker config
MNEMONIC = sys.argv[2]
FETCH_CONFIG_PATH = (
    Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/root/.buncker/config.json")
)

salt_bytes = base64.b64decode(DAEMON_SALT)
aes_key, _hmac_key = derive_keys(MNEMONIC, salt_bytes)
marker = b"buncker-pair-check"
derived_key_check = base64.b64encode(encrypt(marker, aes_key)).decode()

config = {
    "salt": DAEMON_SALT,
    "derived_key_check": derived_key_check,
    "registries": {},
}

FETCH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
FETCH_CONFIG_PATH.write_text(json.dumps(config, indent=2))
print(f"OK: synced salt to {FETCH_CONFIG_PATH}")
