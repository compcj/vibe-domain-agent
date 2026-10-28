from app.worker import celery_app, runtime_state


@celery_app.task(name="app.knowledge.tasks.index_document")
def index_document_task(identifier):
    from app.knowledge.indexing import index_document

    index_document(runtime_state(), identifier)


@celery_app.task(name="app.knowledge.tasks.recover")
def recover_indexing_task():
    from app.knowledge.indexing import recover_indexing

    return recover_indexing(runtime_state())


@celery_app.task(name="app.knowledge.tasks.cleanup_files")
def cleanup_files_task():
    from app.knowledge.lifecycle import cleanup_files

    return cleanup_files(runtime_state())
