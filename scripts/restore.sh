#!/usr/bin/env bash
# Restore into a NEW Compose project with empty volumes and the backed-up config.env.
set -euo pipefail
snapshot="${1:?Provide an extracted snapshot directory}"
[[ "${2:-}" == '--confirm-empty-target' ]] || { echo 'Pass --confirm-empty-target for a new, empty deployment'; exit 1; }
[[ -f "$snapshot/database.dump" && -f "$snapshot/files.tar.gz" && -f "$snapshot/config.env" && -f .env ]] || { echo 'Missing snapshot files or target .env'; exit 1; }
cmp -s .env "$snapshot/config.env" || { echo 'Restore .env from snapshot config.env first (preserves encryption keys)'; exit 1; }
docker compose up -d --wait db redis
tables="$(docker compose exec -T db psql -U vda -d vda -Atc "select count(*) from information_schema.tables where table_schema='public'")"
[[ "$tables" == '0' ]] || { echo 'Target database is not empty; no changes made'; exit 1; }
docker compose run --rm --no-deps -T api python /app/scripts/filesnapshot.py restore < "$snapshot/files.tar.gz"
docker compose exec -T db pg_restore -U vda -d vda --no-owner --exit-on-error < "$snapshot/database.dump"
docker compose up -d --wait
echo 'Restore completed. Verify conversations, citations, pending approvals and external operation outcomes before reopening traffic.'
