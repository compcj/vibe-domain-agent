"""Docker test boundary: real recovery helpers run against fixture-mounted directories."""
import json
import os
import subprocess
import sys
from pathlib import Path


def host_path(value):
    if os.name == 'nt' and len(value) > 3 and value[0] == '/' and value[2] == '/':
        return value[1] + ':' + value[2:]
    return value


root = Path(os.environ['RECOVERY_TEST_ROOT'])
a = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as log:
    log.write(json.dumps({'args': a, 'project': os.environ.get('COMPOSE_PROJECT_NAME'), 'port': os.environ.get('HTTP_PORT'), 'compose_file': os.environ.get('COMPOSE_FILE', '')}) + '\n')
image = json.loads((root / 'image.fixture.json').read_text())
containers = json.loads((root / 'containers.fixture.json').read_text())
if a[:2] == ['compose', 'ps']:
    print('cid-' + a[-1])
elif a[0] == 'inspect':
    if '--format' in a:
        print(image[0]['Id'])
    else:
        print(json.dumps([entry for entry in containers if entry['Id'] in a]))
elif a[:2] == ['image', 'inspect']:
    if '--format' in a:
        print(image[0]['Id'])
    else:
        print(json.dumps(image))
elif a[:2] == ['image', 'save']:
    sys.stdout.buffer.write(b'fixture-image-archive')
elif a[:2] in [['image', 'load'], ['image', 'tag'], ['pull', '--platform']]:
    pass
elif a[0] == 'run':
    mounts = {}
    for index, arg in enumerate(a):
        if arg in ('-v', '--volume'):
            host, container = a[index + 1].rsplit(':', 1)
            if container == 'ro':
                host, container = host.rsplit(':', 1)
            mounts[container] = host_path(host)
    helper_index = next(i for i, arg in enumerate(a) if arg.startswith('/helpers/'))
    helper = a[helper_index]
    if helper.endswith('filesnapshot.py'):
        if a[helper_index + 1] == 'create':
            sys.stdout.buffer.write(b'fixture-files-archive')
        else:
            (root / 'files-restored').write_bytes(sys.stdin.buffer.read())
    else:
        def translate(value):
            for container, host in mounts.items():
                if value == container or value.startswith(container + '/'):
                    return host + value[len(container):]
            return value
        command = [sys.executable, *[translate(arg) for arg in a[helper_index:]]]
        sys.exit(subprocess.run(command).returncode)
elif a[:2] == ['compose', 'exec']:
    if 'pg_dump' in a:
        sys.stdout.buffer.write(b'fixture-database')
    elif 'pg_restore' in a:
        (root / 'database-restored').write_bytes(sys.stdin.buffer.read())
    elif 'psql' in a:
        print('1' if (root / 'nonempty').exists() else '0')
elif a[:2] == ['compose', 'run']:
    if 'create' in a:
        sys.stdout.buffer.write(b'fixture-files-archive')
    elif 'restore' in a:
        (root / 'files-restored').write_bytes(sys.stdin.buffer.read())
elif a[:2] == ['compose', 'up']:
    paths = os.environ.get('COMPOSE_FILE', '').split(':')
    for path in paths:
        if path.endswith('.json'):
            (root / 'restored-compose.json').write_bytes(Path(host_path(path)).read_bytes())
elif a[:2] == ['compose', 'images']:
    print('[]')
