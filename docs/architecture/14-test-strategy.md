# 14. Test Strategy

- **Philosophy:** Test-after. 80% coverage minimum, 100% on crypto.
- **Pyramid:** 70% unit, 25% integration, 5% e2e.
- **Framework:** pytest 8.x, `unittest.mock` for mocking.
- **Integration:** Temp directories, localhost HTTP server, mock OCI registry.
- **E2E:** Full cycle (setup → analyze → generate → fetch → import → pull) in CI.
- **CI Pipeline:** `ruff check → ruff format --check → pytest (unit) → pytest (integration) → pytest (e2e) → coverage`

---
