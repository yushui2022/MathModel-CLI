# Validation Record

Initial local validation: 2026-10-07, Windows, Python 3.11.1.

## Verified Locally

- Offline unit/controller suite: 91 passed, 1 skipped (Windows symlink creation privilege unavailable).
- `ruff check .`: passed.
- `pip check`: passed.
- Source distribution and Python wheel build: passed.
- Real pinned Standard 2.3.0 ZIP: full archive SHA-256 and per-file manifest verified; `prepare --offline`
  created a READY task with original inputs, Skill and scaffold snapshots. No fake archive was used in
  this manual smoke test.
- Pinned Codex 0.160.1 `exec resume --help`: verified UUID resume, JSONL output and ignore-config flags
  without starting a model turn. The machine's global Codex installation was not modified.
- Original MathModel-Skill checkout: unchanged.

## Not Yet Claimed

- Local Docker integration: blocked because Docker Desktop's Linux engine named pipe is unavailable.
  Starting Docker Desktop timed out; no Docker settings or existing virtual disks were moved.
- Real model/API calls, model availability, third-party provider compatibility and end-to-end contest
  paper quality: deliberately NOT tested, per the user's instruction to perform program/offline tests only.
- Remote CI status must be read from the repository's Actions page; configuring a workflow is not proof
  that it passed. CI includes four OS/Python unit combinations and a separate Docker test job.

The controller tests use explicitly labeled fake artifacts to test state/acceptance handling. Those
fixtures are not valid Word/PDF documents and are never presented as accepted scientific output. The
opt-in Docker tests instead run real Codex version detection, read-only/nonroot checks, LibreOffice
rendering and the real pinned Standard evidence gate's rejection of missing artifacts, with no model calls.
