# AGENTS.md

## Scope
This file applies to everything under `v2/`.

## Rules for agents
- Do not modify files outside `v2/`.
- Keep clean architecture boundaries:
  - `domain`: entities + interfaces/ports only.
  - `application`: use-cases and orchestration.
  - `infrastructure`: external implementations (Mongo, SSH, Telegram).
  - `interfaces`: Telegram handlers/adapters.
- Prefer small, testable changes.
- Add/adjust tests in `v2/tests` for behavior changes.
- Keep automation current (`compose.yaml`, `Makefile`, `.env.example`).

## Code quality
- Use type hints.
- Keep functions focused and avoid cross-layer imports that break boundaries.
- Never commit secrets; keep them in `.env` only.
