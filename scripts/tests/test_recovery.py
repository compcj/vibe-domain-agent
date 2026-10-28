"""Exercise backup/restore at the Docker boundary without a Docker daemon."""
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE_ID = 'sha256:' + 'a' * 64
DIGEST = 'ghcr.io/compcj/vibe-domain-agent@sha256:' + 'b' * 64


@pytest.fixture
def deployment(tmp_path):
    bash = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
    if not bash or not Path(bash).is_file():
        pytest.skip('Bash required')
    binary = tmp_path / 'bin'
    binary.mkdir()
    shutil.copytree(ROOT / 'scripts', tmp_path / 'scripts', ignore=shutil.ignore_patterns('__pycache__', 'tests'))
    (tmp_path / 'compose.yaml').write_text('services: {}\n')
    (tmp_path / '.env').write_text('APP_IMAGE=registry.example/app:latest\nSECRET_KEY=stale\nENCRYPTION_KEY=stale\nPOSTGRES_PASSWORD=stale\nHTTP_PORT=8181\nCOMPOSE_PROJECT_NAME=source\n')
    runtime = {'APP_ENV': 'test', 'SECRET_KEY': 'effective-secret-$-value', 'ENCRYPTION_KEY': 'effective-encryption', 'DATABASE_URL': 'postgresql+psycopg://vda:effective-password@db:5432/vda'}
    containers = []
    for service in ['api', 'worker', 'indexer', 'beat', 'db']:
        values = {'POSTGRES_PASSWORD': 'effective-password'} if service == 'db' else runtime
        containers.append({'Id': 'cid-' + service, 'Image': IMAGE_ID, 'Config': {'Labels': {'com.docker.compose.service': service}, 'Env': [f'{key}={value}' for key, value in values.items()]}})
    (tmp_path / 'containers.fixture.json').write_text(json.dumps(containers))
    (tmp_path / 'image.fixture.json').write_text(json.dumps([{'Id': IMAGE_ID, 'RepoDigests': [DIGEST]}]))
    fake = ROOT / 'scripts/tests/fake_recovery_docker.py'
    (binary / 'docker').write_text(f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "{fake.as_posix()}" "$@"\n', newline='\n')
    (binary / 'docker').chmod(0o755)
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'], 'RECOVERY_TEST_ROOT': tmp_path.as_posix(), 'APP_IMAGE': 'registry.example/app:new-latest', 'SECRET_KEY': 'ambient-wrong', 'ENCRYPTION_KEY': 'ambient-wrong', 'COMPOSE_FILE': 'compose.yaml:compose.https.yaml', 'COMPOSE_PROJECT_NAME': 'fresh-target', 'HTTP_PORT': '9292', 'MSYS_NO_PATHCONV': '1'}

    def run(script, *args):
        return subprocess.run([str(bash), f'scripts/{script}', *map(str, args)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=40)

    return tmp_path, run


def backup(deployment):
    root, run = deployment
    result = run('backup.sh')
    assert result.returncode == 0, result.stderr + result.stdout
    extracted = root / 'snapshot'
    extracted.mkdir()
    with tarfile.open(next((root / 'backups').glob('vda-*.tar.gz'))) as archive:
        archive.extractall(extracted, filter='data')
    return extracted, result


@pytest.mark.parametrize('local', [False, True])
def test_restore_pins_actual_runtime_and_preserves_target_settings(deployment, local):
    root, run = deployment
    if local:
        (root / 'image.fixture.json').write_text(json.dumps([{'Id': IMAGE_ID, 'RepoDigests': []}]))
    snapshot, result = backup(deployment)
    assert (snapshot / 'recovery.json').is_file(), 'Backup must record an enforceable immutable manifest'
    manifest = json.loads((snapshot / 'recovery.json').read_text())
    assert manifest['image_id'] == IMAGE_ID
    assert (snapshot / 'application-image.tar').is_file() == local
    config = (snapshot / 'config.env').read_text()
    assert 'effective-encryption' in config and 'stale' not in config
    assert 'effective-secret-$-value' in config
    assert 'effective-secret' not in result.stdout + result.stderr
    (root / '.env').write_text(config.replace('HTTP_PORT=8181', 'HTTP_PORT=9191').replace('COMPOSE_PROJECT_NAME=source', 'COMPOSE_PROJECT_NAME=restored'))
    (root / 'calls.jsonl').write_text('')
    result = run('restore.sh', snapshot.as_posix(), '--confirm-empty-target')
    assert result.returncode == 0, result.stderr + result.stdout
    effective = json.loads((root / 'restored-compose.json').read_text())
    for service in ['api', 'worker', 'indexer', 'beat', 'migrate']:
        assert effective['services'][service]['image'] == manifest['image_reference']
        assert effective['services'][service]['environment']['ENCRYPTION_KEY'] == 'effective-encryption'
        assert effective['services'][service]['environment']['SECRET_KEY'] == 'effective-secret-$$-value'
    env = (root / '.env').read_text()
    assert 'HTTP_PORT=9191' in env and 'COMPOSE_PROJECT_NAME=restored' in env
    calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
    assert all(call['project'] == 'fresh-target' and call['port'] == '9292' for call in calls)
    assert any('compose.https.yaml' in call['compose_file'] for call in calls)
    assert any(call['args'][:2] == ['image', 'load'] for call in calls) == local
    assert (root / 'database-restored').is_file() and (root / 'files-restored').is_file()


@pytest.mark.parametrize('problem', ['missing-manifest', 'wrong-id', 'wrong-runtime-image', 'changed-config', 'different-worker', 'different-worker-key', 'missing-local-image', 'nonempty'])
def test_recovery_refuses_ambiguous_or_nonempty_target(deployment, problem):
    root, run = deployment
    if problem in ('different-worker', 'different-worker-key'):
        containers = json.loads((root / 'containers.fixture.json').read_text())
        if problem == 'different-worker':
            containers[1]['Image'] = 'sha256:' + 'c' * 64
        else:
            containers[1]['Config']['Env'].append('ENCRYPTION_KEY=different-worker-key')
        (root / 'containers.fixture.json').write_text(json.dumps(containers))
        result = run('backup.sh')
        assert result.returncode != 0
        assert not list((root / 'backups').glob('vda-*.tar.gz'))
        return
    if problem == 'missing-local-image':
        (root / 'image.fixture.json').write_text(json.dumps([{'Id': IMAGE_ID, 'RepoDigests': []}]))
    snapshot, _ = backup(deployment)
    if problem == 'missing-manifest':
        (snapshot / 'recovery.json').unlink(missing_ok=True)
    elif problem == 'missing-local-image':
        (snapshot / 'application-image.tar').unlink()
    elif problem == 'wrong-runtime-image':
        (root / 'image.fixture.json').write_text(json.dumps([{'Id': 'sha256:' + 'c' * 64, 'RepoDigests': [DIGEST]}]))
    elif problem == 'wrong-id':
        manifest_path = snapshot / 'recovery.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['image_id'] = 'sha256:' + 'c' * 64
        manifest_path.write_text(json.dumps(manifest))
    elif problem == 'changed-config':
        with (snapshot / 'config.env').open('a') as stream:
            stream.write('ENCRYPTION_KEY=wrong\n')
    else:
        (root / 'nonempty').touch()
    (root / 'calls.jsonl').write_text('')
    result = run('restore.sh', snapshot.as_posix(), '--confirm-empty-target')
    assert result.returncode != 0
    assert not (root / 'database-restored').exists() and not (root / 'files-restored').exists()
    if problem != 'nonempty':
        calls = [json.loads(line)['args'] for line in (root / 'calls.jsonl').read_text().splitlines()]
        assert not any(call[:2] == ['compose', 'up'] for call in calls)
