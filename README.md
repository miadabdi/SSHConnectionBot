# SSHConnection v2

A clean-architecture rebuild of the Telegram SSH bot under `v2/` with near-full feature parity.

## Features

- Multi-session SSH with named sessions
- Password and private-key authentication
- Live output streaming with `sendMessageDraft` + automatic classic fallback
- Interactive shell mode
- Output paging for large results
- Saved servers + quick connect
- Server groups
- Macros
- SFTP upload/download
- System monitor
- Session timeout watchdog
- Optional Telegram user allowlist

## Quick Start (Docker)

1. `cd v2`
2. `make init`
3. `make keygen` and put generated key into `.env` as `ENCRYPTION_KEY`
4. Fill `BOT_TOKEN` in `.env`
5. `make up`
6. `make logs`

## Local Run

```bash
cd v2
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

## Tests and Lint

```bash
cd v2
make test
make lint
```
