# AthenaPanel

A production VPN management panel for a hybrid **L2TP/IPsec + SSTP + WireGuard**
stack — one user account, one shared quota, across all three protocols.

- **Backend** — FastAPI (async SQLAlchemy / asyncpg) + JWT, PostgreSQL 16 +
  TimescaleDB. Self-healing usage accounting, multi-operator RBAC, per-user
  WireGuard provisioning, WARP outbound, signed subscription pages, automated
  backups (local + Telegram).
- **Frontend** — React + Vite + TypeScript + shadcn/ui (dark, served under a
  secret URL path).
- **VPN core** — Libreswan + xl2tpd / accel-ppp + WireGuard.

> **Private repo.** Secrets (`.env`, keys, PSK, database) are **never** committed
> — they live only in the backup bundle you keep privately.

---

## 1. Fresh install (new server)

On a clean Ubuntu 22.04 / 24.04 host, as root:

```bash
# clones this private repo (asks for a GitHub token) and runs the installer
sudo bash bootstrap.sh
```

Or, if the code is already on the box:

```bash
sudo bash athena-setup.sh
```

The installer sets up PostgreSQL 16 + TimescaleDB, the backend (venv), builds the
frontend, configures nginx + systemd + WireGuard, and offers to install the
L2TP/IPsec core. Anything environment-specific (domain, PSK, admin login, VPN
core) is **asked interactively** — nothing is guessed.

## 2. Back up (source server)

Creates one self-contained bundle with the full database + every setting/secret.
pg_dump is read-only, so this never disrupts live users:

```bash
sudo bash athena-backup.sh                 # full  -> /root/athena-backup-<ts>.zip
sudo ESSENTIAL=1 bash athena-backup.sh     # smaller: skips the usage_samples series
```

Keep the bundle **private** (it holds secrets); move it over a trusted channel.

## 3. Restore / migrate (to a new server)

```bash
scp /root/athena-backup-<ts>.zip root@NEW_SERVER:/root/
# on the new server:
sudo BACKUP=/root/athena-backup-<ts>.zip bash athena-setup.sh
#   (or:  sudo BACKUP=/root/athena-backup-<ts>.zip bash bootstrap.sh)
```

The panel comes up **identical** — same users, admins, quotas, WireGuard peers,
accounting ledger and settings — so existing clients keep working unchanged.

---

## Layout

```
athena-setup.sh     one-shot interactive installer (+ restore)
athena-backup.sh    full backup-bundle creator
bootstrap.sh        clone the private repo + run the installer
backend/            FastAPI app (app/) + requirements + alembic
frontend/           React + Vite app
configs/            ppp ip-up/down hooks, accel-ppp configs
deploy/             nginx + systemd unit templates
tune-network.sh     BBR/fq + socket-buffer tuning
```

## Operating notes

- Manage users, admins, WireGuard and backups from the panel UI (served under
  the secret `PANEL_PATH`).
- Users are written to `/etc/ppp/chap-secrets`; accel-ppp reloads automatically.
- Local backups live in Settings → Backups (14 retained). An optional daily
  off-site copy goes to Telegram (falls back to an essential dump if the full
  one exceeds Telegram's 50 MB cap).
- The relay / backhaul topology is environment-specific and configured
  separately from this installer.

## Development

```bash
# backend
cd backend && python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
DATABASE_URL=postgresql+asyncpg://vpnpanel:***@127.0.0.1:5432/vpnpanel \
  uvicorn app.main:app --reload        # http://127.0.0.1:8000/docs

# frontend (proxies /api to :8000)
cd frontend && npm install && npm run dev
```
