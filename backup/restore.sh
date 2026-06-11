#!/usr/bin/env bash
# Restore pivotlab database from backup dump.
# Usage: ./backup/restore.sh
# Prerequisites: docker-compose services must be running (data-sync-db healthy).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DUMP_FILE="$SCRIPT_DIR/pivotlab.dump"

if [ ! -f "$DUMP_FILE" ]; then
    echo "ERROR: $DUMP_FILE not found"
    exit 1
fi

echo "Waiting for data-sync-db to be healthy..."
until docker exec data-sync-db pg_isready -U pivotlab -p 5433 -q 2>/dev/null; do
    sleep 1
done

echo "Restoring database from $DUMP_FILE ..."
docker exec -i data-sync-db pg_restore \
    -U pivotlab -p 5433 -d pivotlab \
    --no-owner --no-privileges --clean --if-exists \
    < "$DUMP_FILE"

echo "Done. Database restored."
