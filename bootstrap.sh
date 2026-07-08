#!/usr/bin/env bash
###############################################################################
#  Athena Panel — bootstrap.  Clones the PRIVATE repo and runs the installer.
#
#      sudo bash bootstrap.sh
#      sudo BACKUP=/root/athena-backup-XXXX.zip bash bootstrap.sh   # + restore
#
#  Prompts once for a GitHub token (a fine-grained/classic PAT with read access
#  to ARN0Y/AthenaPanel). The token is used only for the clone and then scrubbed
#  from the on-disk git config.
###############################################################################
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo bash bootstrap.sh)"; exit 1; }

TTY=/dev/tty
REPO="ARN0Y/AthenaPanel"
BRANCH="${BRANCH:-main}"
DEST="${DEST:-/opt/AthenaPanel-src}"

command -v git >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y git >/dev/null; }

printf '\033[1;36m ? \033[0mGitHub token (PAT, read access to the private repo): ' > "$TTY"
read -rs TOKEN < "$TTY"; printf '\n' > "$TTY"
[ -n "$TOKEN" ] || { echo "a token is required to clone the private repo"; exit 1; }

echo "==> cloning ${REPO} (branch ${BRANCH})"
rm -rf "$DEST"
git clone -q --depth 1 -b "$BRANCH" \
    "https://x-access-token:${TOKEN}@github.com/${REPO}.git" "$DEST" \
    || { echo "[x] clone failed — check the token and repo access"; exit 1; }

# scrub the token: leave a plain remote URL on disk
git -C "$DEST" remote set-url origin "https://github.com/${REPO}.git"
unset TOKEN
echo "==> cloned to ${DEST}; starting installer"

exec bash "$DEST/athena-setup.sh"
