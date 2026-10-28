#!/usr/bin/env bash
# Run from the deployment directory. Requires a short maintenance window.
set -euo pipefail
umask 077
[[ -f .env && -f compose.yaml ]] || { echo 'Run from the deployment directory'; exit 1; }
helpers="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
directory="${BACKUP_DIR:-$PWD/backups}"
mkdir -p "$directory"
directory="$(cd "$directory" && pwd)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="$(mktemp -d "$directory/.vda-backup.XXXXXX")"
stopped=false
cleanup() {
  if "$stopped"; then docker compose start api worker indexer beat nginx >/dev/null || true; fi
  case "$temporary" in "$directory"/.vda-backup.*) rm -rf -- "$temporary" ;; esac
}
trap cleanup EXIT
containers=()
for service in api worker indexer beat db; do
  container="$(docker compose ps -q "$service")"
  [[ -n "$container" && "$container" != *$'\n'* ]] || { echo 'Backup requires one running container per application service and database'; exit 1; }
  containers+=("$container")
done
image_id="$(docker inspect --format '{{.Image}}' "${containers[0]}")"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Invalid running application image'; exit 1; }
docker inspect "${containers[@]}" > "$temporary/containers.json"
docker image inspect "$image_id" > "$temporary/image.json"
cp .env "$temporary/config.env"
docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint python \
  -v "$helpers:/helpers:ro" -v "$temporary:/backup" "$image_id" /helpers/recovery.py capture /backup
# Inspect data includes secrets unrelated to recovery; do not retain it in the snapshot.
rm -- "$temporary/containers.json" "$temporary/image.json"
reference="$(cat "$temporary/image-reference.txt")"
if [[ "$reference" == vda-recovery:* ]]; then
  docker image save "$image_id" > "$temporary/application-image.tar"
fi
stopped=true
docker compose stop -t 360 nginx api worker indexer beat
docker compose exec -T db pg_dump -U vda -d vda -Fc > "$temporary/database.dump"
# Use the stopped running container's volumes and exact image, independent of shell/.env drift.
docker run --rm --network none --volumes-from "${containers[0]}" --entrypoint python \
  -v "$helpers:/helpers:ro" "$image_id" /helpers/filesnapshot.py create > "$temporary/files.tar.gz"
printf '%s\n' "$stamp" > "$temporary/created-at.txt"
tar -czf "$directory/vda-$stamp.tar.gz" -C "$temporary" .
chmod 600 "$directory/vda-$stamp.tar.gz"
find "$directory" -maxdepth 1 -type f -name 'vda-????????T??????Z.tar.gz' -mtime +6 -delete
echo "Backup created: $directory/vda-$stamp.tar.gz"
echo 'Contains encryption keys and private data. Encrypt before offsite transfer.'
