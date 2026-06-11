#!/usr/bin/env bash
# Backup pivotlab database to a compressed dump file.
# Usage: ./backup/backup.sh
# Prerequisites: docker-compose services must be running (data-sync-db healthy).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DUMP_FILE="$SCRIPT_DIR/pivotlab.dump"

echo "Waiting for data-sync-db to be healthy..."
until docker exec data-sync-db pg_isready -U pivotlab -p 5433 -q 2>/dev/null; do
    sleep 1
done

echo "Backing up database to $DUMP_FILE ..."
docker exec data-sync-db pg_dump \
    -U pivotlab -p 5433 -d pivotlab \
    --no-owner --no-privileges --clean --if-exists \
    -F custom > "$DUMP_FILE"

SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "Done. Backup saved: $DUMP_FILE ($SIZE)"
