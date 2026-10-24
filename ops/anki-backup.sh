#!/usr/bin/env bash
# anki-backup.sh — nightly backup of the Anki sync server's data: the collection every device syncs to,
# with its media. Cron runs it at 07:30 UTC (02:30 CDT / 01:30 CST), clear of the 04:00 rollover, mnemosyne's
# 04:15 job, and the Helios stack backup.
#
# Only helios-anki-sync is stopped, for a few seconds, so no process has the collection open while it's copied
# (CLAUDE.md invariant 1). The check afterwards opens the *copy*, never the live file.
#
# Keeps the newest $ANKI_BACKUP_KEEP daily archives (30) and the first archive of each month for 12 months.
# Restore: docs/deploy.md, "Anki backups".
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

STACK="${HELIOS_DIR:-$HOME/helios/stack}"
DEST="${ANKI_BACKUP_DIR:-/mnt/data/helios/backups/anki}"
KEEP="${ANKI_BACKUP_KEEP:-30}"
KEEP_MONTHLY=12
STAMP="$(date -u +%Y%m%d-%H%M)"
OUT="$DEST/daily/anki-sync-$STAMP.tar.gz"
PARTIAL="$OUT.partial"
CHECK="$(mktemp -d)"

mkdir -p "$DEST/daily" "$DEST/monthly"
cleanup() { rm -rf "$CHECK" "$PARTIAL"; }
trap cleanup EXIT

echo "==> Anki backup ($STAMP)"
cd "$STACK"
docker compose stop anki-sync
# Restart even if tar fails: a stopped sync server means no phone sync.
if ! tar czf "$PARTIAL" -C "$STACK/anki-sync" data; then
  docker compose start anki-sync
  echo "!! tar failed"; exit 1
fi
docker compose start anki-sync

echo "--> Checking the copy..."
tar xzf "$PARTIAL" -C "$CHECK"
for col in "$CHECK"/data/*/collection.anki2; do
  python3 - "$col" <<'PY'
import sqlite3, sys, datetime
path = sys.argv[1]
con = sqlite3.connect(path)
# Anki's schema collates deck and notetype names with its own "unicase"; approximate it for the check.
con.create_collation("unicase", lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
ok =con.execute("PRAGMA integrity_check").fetchone()[0]
notes, cards, revlog = (con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("notes", "cards", "revlog"))
last = con.execute("SELECT MAX(id) FROM revlog").fetchone()[0]
last = datetime.datetime.fromtimestamp(last / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if last else "never"
user = path.split("/")[-2]
print(f"    {user}: integrity {ok}; {notes} notes, {cards} cards, {revlog} reviews, last review {last}")
if ok != "ok":
    sys.exit(f"integrity check failed for {user}")
PY
done

mv "$PARTIAL" "$OUT"
MONTH="$(date -u +%Y%m)"
if ! ls "$DEST/monthly/anki-sync-$MONTH"*.tar.gz >/dev/null 2>&1; then
  cp "$OUT" "$DEST/monthly/"
  echo "--> Kept as this month's copy"
fi

echo "--> Pruning (newest $KEEP daily, $KEEP_MONTHLY monthly)..."
ls -1t "$DEST"/daily/anki-sync-*.tar.gz | tail -n +$((KEEP + 1)) | xargs -r rm -v
ls -1t "$DEST"/monthly/anki-sync-*.tar.gz | tail -n +$((KEEP_MONTHLY + 1)) | xargs -r rm -v

echo "==> DONE $(date -u '+%Y-%m-%d %H:%M UTC')  $OUT ($(du -h "$OUT" | cut -f1))"
