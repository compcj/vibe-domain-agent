"""Random private storage names; neither user filenames nor locators become paths."""

import hashlib
from pathlib import Path

from fastapi import HTTPException

from app.knowledge.parsing import MAX_BYTES, ParseFailure, validate_name
from app.models import new_id


def path_for(state, key):
    root = Path(state.settings.storage_dir).resolve()
    path = (root / key).resolve()
    if path.parent != root or not key or "/" in key or "\\" in key:
        raise ValueError("Invalid private storage key")
    return path


def store_upload(state, upload):
    try:
        validate_name(upload.filename)
    except ParseFailure as exc:
        raise HTTPException(422, str(exc)) from None
    data = upload.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File exceeds 20 MiB limit")
    key = new_id()
    path = path_for(state, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key, hashlib.sha256(data).hexdigest()


def remove_file(state, key):
    path_for(state, key).unlink(missing_ok=True)
