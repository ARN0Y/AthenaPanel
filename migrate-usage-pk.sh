#!/usr/bin/env bash
###############################################################################
# Widen usage_samples' primary key from (ts, ifname) to (ts, node_id, ifname).
#
# WHY: every node has a ppp0. While node 1 is alone, (ts, ifname) is unique.
# The moment a second node samples in the same second, the key collides — and
# Postgres rejects the ENTIRE insert batch, not just the duplicate row, so one
# collision silently loses every other node's samples for that tick. This must
# land before any remote node reports.
#
# WHY NOT AUTOMATIC: this rebuilds the index on every chunk of a 12M-row
# hypertable under an exclusive lock. That is not something a panel restart
# should do implicitly.
#
#     bash migrate-usage-pk.sh          # check only, changes nothing
#     bash migrate-usage-pk.sh --apply  # do it
#
# Safe to re-run: it detects a key that is already correct and exits.
###############################################################################
set -euo pipefail

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

PSQL="sudo -u postgres psql -d vpnpanel"
Q() { $PSQL -At -c "$1"; }

CURRENT=$(Q "SELECT pg_get_constraintdef(oid) FROM pg_constraint
             WHERE conrelid='usage_samples'::regclass AND contype='p';")
echo "current primary key: ${CURRENT:-<none>}"

if [[ "$CURRENT" == *"node_id"* ]]; then
    echo "already widened — nothing to do."
    exit 0
fi

ROWS=$(Q "SELECT count(*) FROM usage_samples;")
SIZE=$(Q "SELECT pg_size_pretty(pg_total_relation_size('usage_samples'));")
CHUNKS=$(Q "SELECT count(*) FROM pg_inherits WHERE inhparent='usage_samples'::regclass;")
COMPRESSED=$(Q "SELECT count(*) FROM timescaledb_information.chunks
                WHERE hypertable_name='usage_samples' AND is_compressed;" 2>/dev/null || echo 0)
echo "rows=$ROWS size=$SIZE chunks=$CHUNKS compressed_chunks=$COMPRESSED"

if [ "$COMPRESSED" != "0" ]; then
    echo "[x] compressed chunks present; decompress them first." >&2
    exit 1
fi

# Any row that would violate the new key? There cannot be, since node_id is
# constant today, but never run destructive DDL on an assumption.
DUPES=$(Q "SELECT count(*) FROM (
             SELECT ts, node_id, ifname FROM usage_samples
             GROUP BY 1,2,3 HAVING count(*) > 1) d;")
echo "rows that would violate the new key: $DUPES"
if [ "$DUPES" != "0" ]; then
    echo "[x] duplicates present — resolve them before migrating." >&2
    exit 1
fi

if [ "$APPLY" != "1" ]; then
    echo
    echo "Dry run. Re-run with --apply to perform the change."
    echo "Expect an exclusive lock for roughly the time it takes to rebuild"
    echo "$SIZE of index. Writers block (they do not fail) during it."
    exit 0
fi

echo
echo "applying..."
START=$(date +%s)
# lock_timeout keeps this from queueing behind a long transaction and stalling
# every writer behind US; better to fail fast and retry than to freeze inserts.
$PSQL -v ON_ERROR_STOP=1 <<'SQL'
SET lock_timeout = '15s';
SET statement_timeout = '30min';
BEGIN;
ALTER TABLE usage_samples DROP CONSTRAINT usage_samples_pkey;
ALTER TABLE usage_samples ADD PRIMARY KEY (ts, node_id, ifname);
COMMIT;
SQL
END=$(date +%s)

echo
echo "new primary key: $(Q "SELECT pg_get_constraintdef(oid) FROM pg_constraint
                            WHERE conrelid='usage_samples'::regclass AND contype='p';")"
echo "took $((END-START))s"
echo "rows after: $(Q "SELECT count(*) FROM usage_samples;")  (was $ROWS)"
