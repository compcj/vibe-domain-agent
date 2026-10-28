#!/usr/bin/env bash
# Run from the deployment directory. Requires a short maintenance window.
set -euo pipefail
umask 077
[[ -f .env && -f compose.yaml ]] || { echo 'Run from the deployment directory'; exit 1; }
directory="${BACKUP_DIR:-$PWD/backups}"
mkdir -p "$directory"
directory="$(cd "$directory" && pwd)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="$(mktemp -d "$directory/.vda-backup.XXXXXX")"
resume() { docker compose start api worker indexer beat nginx >/dev/null || true; }
trap resume EXIT
docker compose stop -t 360 nginx api worker indexer beat
docker compose exec -T db pg_dump -U vda -d vda -Fc > "$temporary/database.dump"
docker compose run --rm --no-deps -T api python /app/scripts/filesnapshot.py create > "$temporary/files.tar.gz"
cp .env "$temporary/config.env"
docker compose images --format json > "$temporary/images.json"
printf '%s\n' "$stamp" > "$temporary/created-at.txt"
tar -czf "$directory/vda-$stamp.tar.gz" -C "$temporary" .
chmod 600 "$directory/vda-$stamp.tar.gz"
# Remove only this script's verified temporary directory within the chosen backup directory.
case "$temporary" in "$directory"/.vda-backup.*) rm -rf -- "$temporary" ;; *) exit 1 ;; esac
find "$directory" -maxdepth 1 -type f -name 'vda-????????T??????Z.tar.gz' -mtime +6 -delete
echo "Backup created: $directory/vda-$stamp.tar.gz"
echo 'Contains encryption keys and private data. Encrypt before offsite transfer.'
