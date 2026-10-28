#!/usr/bin/env bash
# Test upgrade and a pre-upgrade backup rollback on disposable projects only.
set -euo pipefail
previous="${1:?Previous image digest is required}"
candidate="${2:?Candidate image digest is required}"
for image in "$previous" "$candidate"; do
  [[ "$image" =~ ^ghcr.io/compcj/vibe-domain-agent@sha256:[a-f0-9]{64}$ ]]
done
root="$(pwd)"
workspace="$(mktemp -d /tmp/vda-upgrade.XXXXXX)"
project="vda-upgrade-${GITHUB_RUN_ID:-local}-$$"
export HTTP_PORT=0 APP_IMAGE="$previous"
cleanup() {
  result=$?
  for side in upgrade rollback; do
    if [[ "$result" != 0 ]]; then
      (cd "$workspace/$side" && COMPOSE_PROJECT_NAME="$project-$side" docker compose logs --tail=50) || true
    fi
    docker rm -f "$project-$side-mock" >/dev/null 2>&1 || true
    (cd "$workspace/$side" && COMPOSE_PROJECT_NAME="$project-$side" docker compose down -v --remove-orphans) >/dev/null 2>&1 || true
  done
  case "$workspace" in /tmp/vda-upgrade.*) rm -rf -- "$workspace" ;; *) exit 1 ;; esac
}
trap cleanup EXIT
for side in upgrade rollback; do
  mkdir -p "$workspace/$side"
  cp "$root/compose.yaml" "$workspace/$side/compose.yaml"
  cp -r "$root/deploy" "$root/scripts" "$workspace/$side/"
done
cd "$workspace/upgrade"
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/output" "$previous" python /app/scripts/configure.py --output /output/.env --development
sed -i 's/^APP_ENV=development$/APP_ENV=test/' .env
sed -i "s|^APP_IMAGE=.*|APP_IMAGE=$previous|" .env
export COMPOSE_PROJECT_NAME="$project-upgrade"
docker compose up -d --wait --wait-timeout 180
docker run -d --name "$project-upgrade-mock" --network "$project-upgrade"_default --network-alias mock -e APP_ENV=test "$previous" python /app/scripts/mock_service.py >/dev/null
for attempt in {1..30}; do
  if docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://mock:9000/business/counts', timeout=2)" >/dev/null 2>&1; then break; fi
  sleep 1
done
docker compose exec -T api python /app/scripts/system_check.py --prepare-restore
bash scripts/backup.sh
mkdir "$workspace/snapshot"
tar -xzf "$(find "$PWD/backups" -name 'vda-*.tar.gz' -type f | head -n1)" -C "$workspace/snapshot"
docker compose stop -t 60 nginx api worker indexer beat

# Upgrade application schema and resume an actual checkpoint created by the prior image.
export APP_IMAGE="$candidate"
docker compose up -d --wait --wait-timeout 180
docker compose exec -T api python /app/scripts/system_check.py --verify-restore
docker compose stop -t 60 nginx api worker indexer beat

# Check whether the old migration runner accepts the new schema; never assume it does.
export APP_IMAGE="$previous"
if docker compose run --rm --no-deps -T api migrate > "$workspace/compatibility.log" 2>&1; then
  compatibility='Previous migration runner accepts the upgraded schema.'
elif grep -q "Can't locate revision identified by" "$workspace/compatibility.log"; then
  compatibility='Previous image rejects the newer schema; rollback requires the pre-upgrade backup.'
else
  cat "$workspace/compatibility.log"
  exit 1
fi
echo "$compatibility"

# Restore the old schema and files into a different, empty deployment. Its simulator
# represents the external system at backup time, not a real external-system rollback.
cd "$workspace/rollback"
cp "$workspace/snapshot/config.env" .env
export COMPOSE_PROJECT_NAME="$project-rollback"
docker compose up -d --wait db redis
docker run -d --name "$project-rollback-mock" --network "$project-rollback"_default --network-alias mock -e APP_ENV=test "$previous" python /app/scripts/mock_service.py >/dev/null
bash scripts/restore.sh "$workspace/snapshot" --confirm-empty-target
docker compose exec -T api python /app/scripts/system_check.py --verify-restore
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo '## Upgrade and backup rollback passed'
    echo "- Previous: \`$previous\`"
    echo "- Candidate: \`$candidate\`"
    echo "- Schema: $compatibility"
    echo '- Upgrade preserved and resumed the pending checkpoint.'
    echo '- Rollback restored the pre-upgrade database/files into a fresh deployment.'
    echo '- External simulators were isolated per branch; real business effects require reconciliation after restore.'
  } >> "$GITHUB_STEP_SUMMARY"
fi
