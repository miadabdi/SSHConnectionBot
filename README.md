# SSHConnection v2

Telegram SSH bot with multi-session support, persistent interactive shells, saved servers, file transfer, and monitoring.

## Features

- Multi-session SSH with named sessions (`/switch`, `/sessions`)
- Password and private-key authentication (including passphrase-protected keys)
- Persistent interactive shell mode (`/shell`) with slash escaping (`//command`)
- Saved servers with reconnect (`/connect <saved_name>`) and optional default cwd
- Upload/download over SFTP
- Server groups and command macros
- Live output streaming + output paging
- System monitor and session timeout watchdog
- Optional Telegram user allowlist

## Main Commands

- Connection:
  - `/connect` (manual wizard)
  - `/connect <saved_name>` (reconnect saved server, opens interactive shell)
  - `/disconnect [name|all]`
  - `/switch <session_name>`
  - `/status`, `/sessions`, `/history`
- Saved servers:
  - `/save <name> [default_cwd]`
  - `/save <name> -` to clear saved default cwd
  - `/servers`, `/delserver <name>`
  - `/quick <name>` (kept as alias)
- Shell:
  - `/shell`, `/cancel`, `/pwd`, `/exit`
  - In interactive mode, commands return clean per-command output (no raw terminal control bytes)
  - To run a remote command starting with `/`, use `//...`
- Files:
  - `/download <remote_path>`
  - Reply to a file/media message with `/upload [remote_path]`
  - Legacy: upload with caption `/upload [remote_path]`
  - If `remote_path` is omitted, upload uses active interactive shell `$PWD`

## Quick Start (Docker)

1. `cd SSHConnectionBot`
2. `make init`
3. `make keygen` and put generated key into `.env` as `ENCRYPTION_KEY`
4. Fill `BOT_TOKEN` in `.env`
5. `make up`
6. `make logs`

## Local Run

```bash
cd SSHConnectionBot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

## Tests and Lint

```bash
cd SSHConnectionBot
make test
make lint
```
