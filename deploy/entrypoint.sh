#!/bin/sh
set -eu
case "${1:-api}" in
  api) exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers ;;
  worker) exec celery -A app.worker.celery_app worker -Q runs --concurrency=10 --loglevel=WARNING --hostname="runs@%h" ;;
  indexer) exec celery -A app.worker.celery_app worker -Q indexing --concurrency=2 --loglevel=WARNING --hostname="indexing@%h" ;;
  beat) exec celery -A app.worker.celery_app beat --loglevel=WARNING --schedule=/app/data/celerybeat-schedule ;;
  migrate|init-admin) exec python -m app.cli "$1" ;;
  *) exec "$@" ;;
esac
