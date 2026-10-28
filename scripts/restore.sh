#!/usr/bin/env bash
# Restore only a verified snapshot into a NEW Compose project with empty volumes.
set -euo pipefail
umask 077
snapshot="${1:?Provide an extracted snapshot directory}"
[[ "${2:-}" == '--confirm-empty-target' ]] || { echo 'Pass --confirm-empty-target for a new, empty deployment'; exit 1; }
[[ -f .env && -f compose.yaml ]] || { echo 'Run from the target deployment directory with .env'; exit 1; }
for file in database.dump files.tar.gz config.env recovery.json image-id.txt image-reference.txt; do
  [[ -f "$snapshot/$file" ]] || { echo 'Missing verified recovery metadata or snapshot files; legacy snapshots need manual image/key verification'; exit 1; }
done
snapshot="$(cd "$snapshot" && pwd)"
helpers="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image_id="$(cat "$snapshot/image-id.txt")"
reference="$(cat "$snapshot/image-reference.txt")"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Invalid snapshot image identity'; exit 1; }
if [[ "$reference" == "vda-recovery:${image_id#sha256:}" ]]; then
  [[ -s "$snapshot/application-image.tar" ]] || { echo 'Missing local application image archive'; exit 1; }
  docker image load < "$snapshot/application-image.tar" >/dev/null
  docker image tag "$image_id" "$reference"
elif [[ "$reference" =~ ^[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}$ ]]; then
  if ! docker image inspect "$reference" >/dev/null 2>&1; then docker pull --platform linux/amd64 "$reference" >/dev/null; fi
else
  echo 'Snapshot image must be an immutable digest or an archived local image'; exit 1
fi
actual_id="$(docker image inspect --format '{{.Id}}' "$reference")"
[[ "$actual_id" == "$image_id" ]] || { echo 'Snapshot image identity mismatch; target untouched'; exit 1; }
temporary="$(mktemp -d "$PWD/.env.recovery.XXXXXX")"
cleanup() { case "$temporary" in "$PWD"/.env.recovery.*) rm -rf -- "$temporary" ;; esac; }
trap cleanup EXIT
docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint python \
  -v "$helpers:/helpers:ro" -v "$snapshot:/backup:ro" -v "$PWD:/target:ro" -v "$temporary:/work" \
  "$image_id" /helpers/recovery.py prepare /backup --target /target --output /work
# Append to the caller's HTTPS/custom stack; protected values are explicit in this last overlay.
export COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}:$temporary/recovery.compose.json"
docker compose up -d --wait db redis
tables="$(docker compose exec -T db psql -U vda -d vda -Atc "select count(*) from information_schema.tables where table_schema='public'")"
[[ "$tables" == '0' ]] || { echo 'Target database is not empty; no data restored'; exit 1; }
# Keep the immutable reference and effective keys for subsequent normal starts; retain target ports/project.
cp "$temporary/config.env" .env
chmod 600 .env
docker compose run --rm --no-deps -T -v "$helpers:/helpers:ro" api python /helpers/filesnapshot.py restore < "$snapshot/files.tar.gz"
docker compose exec -T db pg_restore -U vda -d vda --no-owner --exit-on-error < "$snapshot/database.dump"
docker compose up -d --wait
echo 'Restore completed. Verify conversations, citations, pending approvals and external operation outcomes before reopening traffic.'
