#!/usr/bin/env bash
###############################################################################
# set-multiplier.sh — change the hidden accounting multiplier (USAGE_MULTIPLIER)
#
#   ./set-multiplier.sh          show the current value and exit
#   ./set-multiplier.sh 1.4      bill 1.4x the measured traffic   (+40%)
#   ./set-multiplier.sh 40%      the same thing, written as a percentage
#   ./set-multiplier.sh 1.0      exact billing (multiplier effectively off)
#
# Restarts ONLY vpn-panel. Connected VPN users are NOT dropped — xl2tpd,
# accel-ppp (SSTP), WireGuard and IPsec are never touched by this.
#
# The change is FORWARD-LOOKING: traffic already committed to a user's
# used_bytes is NOT re-scaled; only new traffic is billed at the new rate.
#
# If the panel fails to come back up, the previous .env is restored
# automatically.
###############################################################################
set -euo pipefail

ENV_FILE=/opt/vpn-panel/.env
APPDIR=/opt/vpn-panel/backend
VENV="$APPDIR/venv/bin/python"
SERVICE=vpn-panel
KEY=USAGE_MULTIPLIER

bold=$'\033[1m'; grn=$'\033[1;32m'; red=$'\033[1;31m'; ylw=$'\033[1;33m'
dim=$'\033[2m'; off=$'\033[0m'
die()  { printf '%s[x]%s %s\n' "$red" "$off" "$*" >&2; exit 1; }
info() { printf '%s==>%s %s\n' "$bold" "$off" "$*"; }
ok()   { printf '    %s\xe2\x9c\x93%s %s\n' "$grn" "$off" "$*"; }

[ "$(id -u)" -eq 0 ] || die "Run as root."
[ -f "$ENV_FILE" ]   || die "$ENV_FILE not found — is the panel installed here?"

pct_of() { awk -v v="$1" 'BEGIN{ printf "%+.0f", (v-1)*100 }'; }

current="$(grep -E "^${KEY}=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
current="${current:-1.0}"

# ---- no argument: just report -------------------------------------------
if [ $# -eq 0 ]; then
    printf '%sCurrent multiplier:%s %s%s%s  (%s%% vs. measured traffic)\n\n' \
        "$bold" "$off" "$grn" "$current" "$off" "$(pct_of "$current")"
    cat <<EOF
${dim}Usage:${off}
  $0 1.4      bill 1.4x measured traffic   (+40%)
  $0 40%      same, written as a percentage
  $0 1.15     +15%
  $0 1.0      exact billing (off)

${dim}Only vpn-panel restarts — no VPN user is disconnected.
Applies to NEW traffic only; already-recorded usage is not re-scaled.${off}
EOF
    exit 0
fi

# ---- parse the requested value ------------------------------------------
raw="$1"
if [ "${raw%\%}" != "$raw" ]; then                       # given as "40%"
    p="${raw%\%}"
    printf '%s' "$p" | grep -qE '^[0-9]+(\.[0-9]+)?$' \
        || die "'$raw' is not a valid percentage (try 40%)."
    value="$(awk -v p="$p" 'BEGIN{ printf "%.2f", 1 + p/100 }')"
else                                                      # given as "1.4"
    printf '%s' "$raw" | grep -qE '^[0-9]+(\.[0-9]+)?$' \
        || die "'$raw' is not a number (try 1.4 or 40%)."
    if awk -v v="$raw" 'BEGIN{ exit !(v > 3) }'; then
        die "$raw looks like a percentage. Did you mean ${raw}% ?"
    fi
    value="$(awk -v v="$raw" 'BEGIN{ printf "%.2f", v }')"
fi

awk -v v="$value" 'BEGIN{ exit !(v >= 1.0 && v <= 3.0) }' \
    || die "Refusing $value — allowed range is 1.00 to 3.00 (1.00 = off)."

if [ "$value" = "$(awk -v v="$current" 'BEGIN{printf "%.2f", v}')" ]; then
    printf '%sAlready set to %s (%s%%).%s Re-applying anyway.\n' \
        "$ylw" "$value" "$(pct_of "$value")" "$off"
fi

info "Changing multiplier: ${current} -> ${value}  ($(pct_of "$current")% -> $(pct_of "$value")%)"

# ---- apply ---------------------------------------------------------------
backup="${ENV_FILE}.bak-mult-$(date +%F-%H%M%S)"
cp -a "$ENV_FILE" "$backup"
ok "backed up .env -> $backup"

# Keep only the 10 most recent backups so they don't pile up over time.
ls -1t "${ENV_FILE}".bak-mult-* 2>/dev/null | tail -n +11 | xargs -r rm -f

if grep -qE "^${KEY}=" "$ENV_FILE"; then
    sed -i "s|^${KEY}=.*|${KEY}=${value}|" "$ENV_FILE"
else
    printf '\n# Accounting multiplier (server-side only, never shown in the panel)\n%s=%s\n' \
        "$KEY" "$value" >> "$ENV_FILE"
fi
ok "wrote ${KEY}=${value}"

before="$(ip -o link 2>/dev/null | grep -c ppp || echo 0)"
systemctl restart "$SERVICE"
sleep 5

if ! systemctl is-active --quiet "$SERVICE"; then
    printf '%s[!]%s %s failed to start — restoring the previous .env\n' "$red" "$off" "$SERVICE"
    cp -a "$backup" "$ENV_FILE"
    systemctl restart "$SERVICE" || true
    die "Rolled back. Check: journalctl -u $SERVICE -n 40"
fi
after="$(ip -o link 2>/dev/null | grep -c ppp || echo 0)"
ok "$SERVICE restarted (sessions: ${before} -> ${after})"

# ---- verify what the running panel actually loaded ------------------------
effective="$(cd "$APPDIR" && set -a && . "$ENV_FILE" && set +a && \
    "$VENV" -c 'from app.config import settings; print(settings.usage_multiplier)' 2>/dev/null || echo "?")"

printf '\n%sDone.%s Effective multiplier in the running panel: %s%s%s  (%s%%)\n' \
    "$bold" "$off" "$grn" "$effective" "$off" "$(pct_of "${effective:-1}")"
printf '%s1 GB of real traffic is now billed as %s GB.%s\n' \
    "$dim" "$(awk -v v="$effective" 'BEGIN{printf "%.2f", v}')" "$off"
