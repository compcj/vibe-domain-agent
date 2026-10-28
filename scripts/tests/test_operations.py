import base64
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(script, *args, data=None):
    return subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / script), *map(str, args)],
        input=data, capture_output=True, check=False,
    )


def test_configuration_is_random_private_and_never_overwrites(tmp_path):
    target = tmp_path / '.env'
    result = run('configure.py', '--output', target)
    assert result.returncode == 0
    values = dict(line.split('=', 1) for line in target.read_text().splitlines())
    assert len(base64.urlsafe_b64decode(values['ENCRYPTION_KEY'])) == 32
    assert len(values['SECRET_KEY']) == 64
    assert values['SECRET_KEY'].encode() not in result.stdout
    assert values['APP_ENV'] == 'production'
    original = target.read_bytes()
    assert run('configure.py', '--output', target).returncode != 0
    assert target.read_bytes() == original
    if os.name != 'nt':
        assert target.stat().st_mode & 0o777 == 0o600


def test_snapshot_restores_into_initialized_empty_volume(tmp_path):
    source = tmp_path / 'source'
    (source / 'files').mkdir(parents=True)
    (source / 'files' / 'document.txt').write_text('private source', encoding='utf-8')
    (source / 'runtime.db').write_bytes(b'checkpoint bytes')
    archive = run('filesnapshot.py', 'create', '--directory', source)
    assert archive.returncode == 0
    target = tmp_path / 'target'
    (target / 'files').mkdir(parents=True)
    restored = run('filesnapshot.py', 'restore', '--directory', target, data=archive.stdout)
    assert restored.returncode == 0, restored.stderr
    assert (target / 'files' / 'document.txt').read_text() == 'private source'
    assert (target / 'runtime.db').read_bytes() == b'checkpoint bytes'
    assert run('filesnapshot.py', 'restore', '--directory', target, data=archive.stdout).returncode != 0


def test_restore_rejects_traversal_and_symlinks(tmp_path):
    for name, member_type in [('data/../../escape', tarfile.REGTYPE), ('data/link', tarfile.SYMTYPE)]:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            member = tarfile.TarInfo(name)
            member.type = member_type
            member.linkname = '/etc/passwd' if member_type == tarfile.SYMTYPE else ''
            archive.addfile(member)
        assert run('filesnapshot.py', 'restore', '--directory', tmp_path, data=buffer.getvalue()).returncode != 0
    assert not (tmp_path.parent / 'escape').exists()
