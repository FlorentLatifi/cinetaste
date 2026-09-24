#!/usr/bin/env bash
#
# Dump the database, keep the last N dumps, and verify the dump is readable.
#
# A backup nobody has restored is a hope, not a backup — so this refuses to
# report success until pg_restore has listed the archive's contents. That
# catches the usual failure (a truncated or zero-byte file written when the
# disk filled or the connection dropped) at the time it happens rather than on
# the day you need it.
#
#   ./backup_database.sh                      # uses DATABASE_URL
#   ./backup_database.sh /var/backups 14      # directory, retention
#
# Cron it wherever you keep it (GitHub Actions, a cheap VPS, a laptop):
#   0 3 * * *  /path/to/backup_database.sh /var/backups 14 >> /var/log/ct-backup.log 2>&1
#
# Restoring: see docs/RUNBOOK.md. Read it before you need it.

set -euo pipefail

OUT_DIR="${1:-./backups}"
KEEP="${2:-7}"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set." >&2
  exit 2
fi

# The app uses postgresql+asyncpg://; pg_dump speaks plain postgresql://.
DUMP_URL="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
DUMP_URL="${DUMP_URL/postgres+asyncpg:/postgresql:}"

for tool in pg_dump pg_restore; do
  command -v "$tool" >/dev/null || { echo "$tool is not on PATH." >&2; exit 2; }
done

mkdir -p "$OUT_DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$OUT_DIR/cinetaste-$stamp.dump"

echo "Dumping to $target"
# -Fc: custom format, compressed, and restorable table by table.
pg_dump "$DUMP_URL" --format=custom --no-owner --no-privileges --file="$target"

size=$(wc -c < "$target" | tr -d ' ')
if [ "$size" -lt 1024 ]; then
  echo "Dump is only ${size} bytes — treating as failed." >&2
  rm -f "$target"
  exit 1
fi

# Verify: can pg_restore read the archive, and does it contain the tables we
# would actually need back?
tables=$(pg_restore --list "$target" | grep -c "TABLE DATA" || true)
if [ "$tables" -lt 5 ]; then
  echo "Archive lists only ${tables} tables with data — treating as failed." >&2
  exit 1
fi

for required in users interaction_events taste_profiles; do
  pg_restore --list "$target" | grep -q " $required\$\| $required " || {
    echo "Archive is missing '$required' — treating as failed." >&2
    exit 1
  }
done

echo "OK: $(du -h "$target" | cut -f1), ${tables} tables with data"

# Retention. Newest first, delete past the keep count.
if [ "$KEEP" -gt 0 ]; then
  removed=$(ls -1t "$OUT_DIR"/cinetaste-*.dump 2>/dev/null | tail -n "+$((KEEP + 1))" || true)
  if [ -n "$removed" ]; then
    echo "$removed" | while read -r old; do
      echo "Removing $old"
      rm -f "$old"
    done
  fi
fi

echo "Backups on disk: $(ls -1 "$OUT_DIR"/cinetaste-*.dump 2>/dev/null | wc -l | tr -d ' ')"
