#!/usr/bin/env bash
# Exercises the documented backup/restore commands on isolated disposable Compose projects.
set -euo pipefail
image="${1:?Provide a locally available image}"
root="$(pwd)"
workspace="$(mktemp -d /tmp/vda-compose.XXXXXX)"
project="vda-restore-${GITHUB_RUN_ID:-local}-$$"
export APP_IMAGE="$image" HTTP_PORT=0
cleanup() {
  result=$?
  if [[ "$result" != 0 ]]; then
    for side in source restored; do
      (cd "$workspace/$side" && COMPOSE_PROJECT_NAME="$project-$side" docker compose ps -a && COMPOSE_PROJECT_NAME="$project-$side" docker compose logs --tail=60) || true
    done
  fi
  docker rm -f "$project-mock" >/dev/null 2>&1 || true
  for side in source restored; do
    (cd "$workspace/$side" && COMPOSE_PROJECT_NAME="$project-$side" docker compose down -v --remove-orphans) >/dev/null 2>&1 || true
  done
  case "$workspace" in /tmp/vda-compose.*) rm -rf -- "$workspace" ;; *) exit 1 ;; esac
}
trap cleanup EXIT
for side in source restored; do
  mkdir -p "$workspace/$side"
  cp "$root/compose.yaml" "$workspace/$side/compose.yaml"
  cp -r "$root/deploy" "$root/scripts" "$workspace/$side/"
done
cd "$workspace/source"
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/output" "$image" python /app/scripts/configure.py --output /output/.env --development
sed -i 's/^APP_ENV=development$/APP_ENV=test/' .env
export COMPOSE_PROJECT_NAME="$project-source"
docker compose up -d --wait --wait-timeout 180
docker run -d --name "$project-mock" --network "$project-source"_default --network-alias mock -e APP_ENV=test "$image" python /app/scripts/mock_service.py >/dev/null
for attempt in {1..30}; do
  if docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://mock:9000/business/counts', timeout=2)" >/dev/null 2>&1; then break; fi
  sleep 1
done
docker compose exec -T api python /app/scripts/system_check.py --prepare-restore
bash scripts/backup.sh
docker compose stop -t 30 nginx api worker indexer beat
mkdir "$workspace/snapshot"
tar -xzf "$(find "$PWD/backups" -name 'vda-*.tar.gz' -type f | head -n1)" -C "$workspace/snapshot"
cd "$workspace/restored"
cp "$workspace/snapshot/config.env" .env
export COMPOSE_PROJECT_NAME="$project-restored"
docker compose up -d --wait db redis
docker network connect --alias mock "$project-restored"_default "$project-mock"
bash scripts/restore.sh "$workspace/snapshot" --confirm-empty-target
docker compose exec -T api python /app/scripts/system_check.py --verify-restore
echo 'Compose deployment and joint backup/restore smoke passed.'
