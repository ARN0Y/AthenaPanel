#!/usr/bin/env bash
###############################################################################
# Merge users/admins from an OLD panel DB into THIS server's live DB,
# WITHOUT clobbering the live server's accumulated usage.
#
#   # on OLD server:  make a clean snapshot and copy it over
#   sqlite3 /var/lib/vpn-panel/vpn.db ".backup '/root/old.db'"
#   scp /root/old.db root@NEW:/root/
#
#   # on NEW (this) server:
#   sudo bash sync-users.sh /root/old.db
#
# Rules:
#   * user missing here      -> inserted (full copy, incl. its used_bytes ~0)
#   * user already here       -> password/quota/rate/active/expiry/note updated;
#                                used_bytes / last_seen / total_sessions KEPT
#   * admin missing here      -> inserted (so resellers exist); existing kept
# Then vpn-panel is restarted so chap-secrets is regenerated from the DB.
###############################################################################
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

OLD="${1:-/root/old.db}"
LIVE=/var/lib/vpn-panel/vpn.db
[ -f "$OLD" ]  || { echo "old DB not found: $OLD"; exit 1; }
[ -f "$LIVE" ] || { echo "live DB not found: $LIVE"; exit 1; }

python3 - "$OLD" "$LIVE" <<'PY'
import sqlite3, sys
old_path, live_path = sys.argv[1], sys.argv[2]
live = sqlite3.connect(live_path)
live.execute("ATTACH DATABASE ? AS old", (old_path,))

# columns to sync on an EXISTING user (never touch usage counters)
upd_cols = ["password_hash","quota_bytes","rate_up_kbps","rate_down_kbps",
            "is_active","expires_at","note","created_by_admin_id"]

ins, updated = 0, 0
old_users = live.execute("SELECT * FROM old.users").fetchall()
cols = [d[0] for d in live.execute("SELECT * FROM old.users LIMIT 1").description]
ci = {c:i for i,c in enumerate(cols)}

for row in old_users:
    uname = row[ci["username"]]
    exists = live.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
    if exists:
        sets = ", ".join(f"{c}=?" for c in upd_cols if c in ci)
        vals = [row[ci[c]] for c in upd_cols if c in ci] + [uname]
        live.execute(f"UPDATE users SET {sets} WHERE username=?", vals)
        updated += 1
    else:
        insert_cols = [c for c in cols if c != "id"]
        ph = ",".join("?" for _ in insert_cols)
        vals = [row[ci[c]] for c in insert_cols]
        live.execute(f"INSERT INTO users ({','.join(insert_cols)}) VALUES ({ph})", vals)
        ins += 1

# admins: insert any missing by username
acols = [d[0] for d in live.execute("SELECT * FROM old.admins LIMIT 1").description]
aci = {c:i for i,c in enumerate(acols)}
a_ins = 0
for row in live.execute("SELECT * FROM old.admins").fetchall():
    uname = row[aci["username"]]
    if not live.execute("SELECT id FROM admins WHERE username=?", (uname,)).fetchone():
        insert_cols = [c for c in acols if c != "id"]
        ph = ",".join("?" for _ in insert_cols)
        vals = [row[aci[c]] for c in insert_cols]
        live.execute(f"INSERT INTO admins ({','.join(insert_cols)}) VALUES ({ph})", vals)
        a_ins += 1

live.commit()
print(f"users: +{ins} new, {updated} updated   admins: +{a_ins} new")
PY

echo "restarting vpn-panel to regenerate chap-secrets..."
systemctl restart vpn-panel
sleep 2
echo "chap-secrets users now: $(grep -c '^"' /etc/ppp/chap-secrets 2>/dev/null || echo 0)"
