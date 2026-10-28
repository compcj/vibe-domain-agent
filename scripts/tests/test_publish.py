"""Exercise registry publication guards without contacting an external registry."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('scenario', ['absent', 'existing', 'advanced', 'failed-smoke', 'registry-fault'])
def test_publication_preserves_sha_tags_and_gates_latest(tmp_path, scenario):
    bash = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
    if not bash or not Path(bash).is_file():
        pytest.skip('Bash is required for publication orchestration tests')
    binary = tmp_path / 'bin'
    scripts = tmp_path / 'scripts'
    binary.mkdir()
    scripts.mkdir()
    docker = binary / 'docker'
    docker.write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$CALLS"
case "$1 $2" in
  'manifest inspect')
    case "$SCENARIO" in
      absent) echo 'no such manifest: requested image' >&2; exit 1 ;;
      registry-fault) echo 'proxy route not found' >&2; exit 1 ;;
    esac ;;
  'image inspect') printf '%s@sha256:%064d\\n' "$IMAGE" 1 ;;
esac
''', encoding='utf-8', newline='\n')
    gh = binary / 'gh'
    gh.write_text('''#!/usr/bin/env bash
set -eu
if [[ "$SCENARIO" == advanced ]]; then printf '%040d\\n' 2; else printf '%s\\n' "$SOURCE_SHA"; fi
''', encoding='utf-8', newline='\n')
    (scripts / 'image-smoke.sh').write_text('''#!/usr/bin/env bash
set -eu
echo "smoke $1 $2" >> "$CALLS"
[[ "$SCENARIO" != failed-smoke ]]
''', encoding='utf-8', newline='\n')
    docker.chmod(0o755)
    gh.chmod(0o755)
    log = tmp_path / 'calls.log'
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
           'CALLS': log.as_posix(), 'SCENARIO': scenario, 'IMAGE': 'ghcr.io/compcj/vibe-domain-agent',
           'SOURCE_SHA': 'a' * 40, 'GH_TOKEN': 'fixture-token', 'GITHUB_ACTOR': 'fixture',
           'GITHUB_REPOSITORY': 'compcj/vibe-domain-agent', 'GITHUB_STEP_SUMMARY': (tmp_path / 'summary.md').as_posix()}
    result = subprocess.run([str(bash), (ROOT / 'scripts/publish-ghcr.sh').as_posix()],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    calls = log.read_text().splitlines()
    mutations = [line for line in calls if line.startswith(('tag ', 'push '))]
    promotions = [line for line in calls if line.startswith('buildx imagetools create ')]
    if scenario in ('registry-fault', 'failed-smoke'):
        assert result.returncode != 0
        assert not mutations and not promotions
    else:
        assert result.returncode == 0, result.stderr
        assert bool(mutations) == (scenario == 'absent')
        assert bool(promotions) == (scenario != 'advanced')
        assert any(line.startswith('smoke ghcr.io/compcj/vibe-domain-agent@sha256:') for line in calls)
    assert not any('fixture-token' in line for line in calls)
