# 11. Infrastructure and Deployment

## Deployment Strategy

- **Strategy:** Manual .deb installation (air-gap requirement)
- **CI/CD:** GitHub Actions - lint → test → build .deb → artifacts
- **Auto-update:** request.json.enc includes `buncker_version`. buncker-fetch includes newer .deb in response.tar.enc if available. Manual install by operator.

## Environments

- **Dev:** Local machine, `python3 -m buncker`, store in `/tmp/buncker-dev/`
- **CI:** GitHub Actions, Ubuntu latest
- **Production offline:** Debian 12+ dedicated machine, systemd service
- **Production online:** Operator workstation, Debian 12+

## systemd Unit

```ini
[Unit]
Description=Buncker - Offline Docker Registry
After=network.target

[Service]
Type=simple
User=buncker
Group=buncker
ExecStart=/usr/bin/buncker serve
Restart=on-failure
RestartSec=5
WorkingDirectory=/var/lib/buncker
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/buncker /var/log/buncker
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

## Rollback

`dpkg -i buncker_<previous_version>.deb`. Store persists across upgrades. Config protected via `conffiles`.

---
