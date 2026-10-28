"""Celery entrypoint: celery -A app.worker.celery_app worker -Q runs,indexing."""

from functools import lru_cache

from celery import Celery
from kombu import Queue

from app.config import get_settings

settings = get_settings()
celery_app = Celery("domain_agent", broker=settings.redis_url or "memory://")
celery_app.conf.update(
    imports=("app.knowledge.tasks",),
    task_queues=(Queue("runs"), Queue("indexing")),
    task_default_queue="runs",
    task_routes={
        "app.worker.execute_run": {"queue": "runs"},
        "app.worker.resume_run": {"queue": "runs"},
        "app.knowledge.tasks.*": {"queue": "indexing"},
    },
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=330,
    task_time_limit=360,
    broker_transport_options={"visibility_timeout": 450},
    beat_schedule={
        "cleanup-files": {"task": "app.knowledge.tasks.cleanup_files", "schedule": 30.0},
        "recover-indexing": {"task": "app.knowledge.tasks.recover", "schedule": 30.0},
        "recover-runs": {"task": "app.worker.recover_runs", "schedule": 30.0},
        "cleanup-retention": {"task": "app.worker.cleanup_retention", "schedule": 86400.0},
    },
)


@lru_cache
def runtime_state():
    from app.main import create_app

    return create_app(get_settings()).state


def perform(run_id, resume=False):
    from app.runtime.engine import execute

    accepted = execute(runtime_state(), run_id, resume)
    if accepted is False:
        (resume_run if resume else execute_run).apply_async(args=[run_id], countdown=5)


@celery_app.task(name="app.worker.execute_run")
def execute_run(run_id):
    perform(run_id)


@celery_app.task(name="app.worker.resume_run")
def resume_run(run_id):
    perform(run_id, True)


@celery_app.task(name="app.worker.recover_runs")
def recover_runs():
    from app.runtime.recovery import recover

    return recover(runtime_state())


@celery_app.task(name="app.worker.cleanup_retention")
def cleanup_retention():
    from app.runtime.recovery import cleanup

    return cleanup(runtime_state())
