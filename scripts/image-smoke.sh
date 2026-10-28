#!/usr/bin/env bash
# Runs only disposable containers; never connects to the deployment Compose project.
set -euo pipefail
image="${1:?Image reference is required}"
expected_sha="${2:-}"
namespace="vda-smoke-${GITHUB_RUN_ID:-local}-$$"
network="$namespace-net"
cleanup() {
  docker rm -f "$namespace-api" "$namespace-worker" "$namespace-indexer" "$namespace-db" "$namespace-redis" "$namespace-mock" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker volume rm "$namespace-data" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create "$network" >/dev/null
docker volume create "$namespace-data" >/dev/null
# Test-only ephemeral credentials; no production data or credentials are used.
db_password="$(openssl rand -hex 24)"
secret="$(openssl rand -hex 32)"
encryption="$(openssl rand -base64 32 | tr '+/' '-_')"
environment=(-e APP_ENV=test -e "SECRET_KEY=$secret" -e "ENCRYPTION_KEY=$encryption"
  -e "DATABASE_URL=postgresql+psycopg://vda:$db_password@db:5432/vda" -e REDIS_URL=redis://redis:6379/0
  -v "$namespace-data:/app/data")
docker run -d --name "$namespace-db" --network "$network" --network-alias db \
  -e POSTGRES_USER=vda -e POSTGRES_DB=vda -e "POSTGRES_PASSWORD=$db_password" pgvector/pgvector:pg17 >/dev/null
docker run -d --name "$namespace-redis" --network "$network" --network-alias redis redis:7.4-alpine >/dev/null
for attempt in {1..60}; do
  if docker exec "$namespace-db" pg_isready -U vda -d vda >/dev/null 2>&1; then break; fi
  sleep 1
done
if [[ -n "$expected_sha" ]]; then
  actual_sha="$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  [[ "$actual_sha" == "$expected_sha" ]] || { echo 'Image revision mismatch'; exit 1; }
fi
[[ "$(docker run --rm --entrypoint id "$image" -u)" != '0' ]] || { echo 'Image must be non-root'; exit 1; }
docker run --rm --network "$network" "${environment[@]}" "$image" migrate
docker run -d --name "$namespace-api" --network "$network" --network-alias api "${environment[@]}" "$image" api >/dev/null
docker run -d --name "$namespace-worker" --network "$network" "${environment[@]}" "$image" worker >/dev/null
docker run -d --name "$namespace-indexer" --network "$network" "${environment[@]}" "$image" indexer >/dev/null
ready=false
for attempt in {1..60}; do
  if docker exec "$namespace-api" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)" >/dev/null 2>&1; then ready=true; break; fi
  sleep 2
done
[[ "$ready" == true ]] || { echo 'API did not become ready'; docker logs "$namespace-api"; exit 1; }
docker exec "$namespace-api" python /app/scripts/smoke_api.py
docker exec "$namespace-api" python -c "from pathlib import Path; Path('/app/data/files/.shared-volume-check').write_text('shared')"
for service in worker indexer; do
  docker exec "$namespace-$service" python -c "from pathlib import Path; assert Path('/app/data/files/.shared-volume-check').read_text() == 'shared'"
done
docker exec "$namespace-api" python -c "from pathlib import Path; Path('/app/data/files/.shared-volume-check').unlink()"
worker_ready=false
for attempt in {1..30}; do
  if docker exec "$namespace-api" celery -A app.worker.celery_app inspect ping --timeout=3 | grep -q '2 nodes online'; then worker_ready=true; break; fi
  sleep 2
done
[[ "$worker_ready" == true ]] || { echo 'Workers did not respond'; docker logs "$namespace-worker"; docker logs "$namespace-indexer"; exit 1; }
docker run -d --name "$namespace-mock" --network "$network" --network-alias mock -e APP_ENV=test "$image" python /app/scripts/mock_service.py >/dev/null
for attempt in {1..30}; do
  if docker exec "$namespace-api" python -c "import urllib.request; urllib.request.urlopen('http://mock:9000/business/counts', timeout=2)" >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$namespace-api" python /app/scripts/system_check.py --duration "${LOAD_DURATION:-0}" --report /tmp/load-report.json
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  echo '### Real-service acceptance result' >> "$GITHUB_STEP_SUMMARY"
  docker exec "$namespace-api" cat /tmp/load-report.json >> "$GITHUB_STEP_SUMMARY"
fi
echo 'Container smoke passed: migration, non-root, API, frontend, Redis, run/indexing workers.'
