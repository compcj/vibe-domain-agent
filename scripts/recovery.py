"""Private recovery metadata, executed with the image's Python and mounted current helpers."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

APPLICATIONS = ('api', 'worker', 'indexer', 'beat')
KEYS = ('APP_ENV', 'SECRET_KEY', 'ENCRYPTION_KEY', 'DATABASE_URL')
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}')
DIGEST = re.compile(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    path.chmod(0o600)


def replace_config(original, values):
    """Preserve deployment-specific settings; single quotes prevent dollar interpolation."""
    retained = []
    for line in original.splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Z_][A-Z_0-9]*)\s*=', line)
        if not match or match[1] not in values:
            retained.append(line)
    for key, value in values.items():
        if not isinstance(value, str) or not value or any(char in value for char in '\r\n\x00'):
            raise ValueError('Invalid recovery configuration')
        escaped = value.replace('\\', '\\\\').replace("'", "\\'")
        retained.append(f"{key}='{escaped}'")
    return '\n'.join(retained) + '\n'


def capture(directory):
    containers = read_json(directory / 'containers.json')
    services = {}
    for item in containers:
        service = item['Config']['Labels']['com.docker.compose.service']
        if service in services:
            raise ValueError('Ambiguous service containers')
        services[service] = item
    if set(services) != {*APPLICATIONS, 'db'}:
        raise ValueError('Missing service containers')
    image = read_json(directory / 'image.json')
    if len(image) != 1 or not IMAGE_ID.fullmatch(image[0]['Id']):
        raise ValueError('Invalid application image identity')
    image = image[0]
    environments = {}
    for service in APPLICATIONS:
        if services[service]['Image'] != image['Id']:
            raise ValueError('Application services use different images')
        environments[service] = dict(entry.split('=', 1) for entry in services[service]['Config']['Env'])
    effective = {key: environments['api'][key] for key in KEYS}
    if any(any(env.get(key) != value for key, value in effective.items()) for env in environments.values()):
        raise ValueError('Application services use different recovery configuration')
    database = dict(entry.split('=', 1) for entry in services['db']['Config']['Env'])
    password = database['POSTGRES_PASSWORD']
    if unquote(urlsplit(effective['DATABASE_URL']).password or '') != password:
        raise ValueError('Application and database passwords differ')
    digests = image.get('RepoDigests') or []
    if any(not DIGEST.fullmatch(value) for value in digests):
        raise ValueError('Invalid repository digest')
    reference = sorted(digests)[0] if digests else 'vda-recovery:' + image['Id'].split(':')[1]
    values = {key: effective[key] for key in KEYS if key != 'DATABASE_URL'}
    values.update(APP_IMAGE=reference, POSTGRES_PASSWORD=password)
    config = directory / 'config.env'
    config.write_text(replace_config(config.read_text(encoding='utf-8'), values), encoding='utf-8')
    config.chmod(0o600)
    manifest = {'version': 1, 'image_id': image['Id'], 'image_reference': reference,
                'image_archive': not bool(digests), 'environment': effective,
                'config_values': values, 'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest()}
    write_json(directory / 'recovery.json', manifest)
    (directory / 'image-id.txt').write_text(image['Id'] + '\n', encoding='ascii')
    (directory / 'image-reference.txt').write_text(reference + '\n', encoding='ascii')


def prepare(directory, target, output):
    manifest = read_json(directory / 'recovery.json')
    image_id = (directory / 'image-id.txt').read_text().strip()
    reference = (directory / 'image-reference.txt').read_text().strip()
    if (manifest['version'] != 1 or manifest['image_id'] != image_id
            or manifest['image_reference'] != reference or not IMAGE_ID.fullmatch(image_id)):
        raise ValueError('Mismatched image manifest')
    local = reference == 'vda-recovery:' + image_id.split(':')[1]
    if not local and not DIGEST.fullmatch(reference):
        raise ValueError('Mutable image reference is not recoverable')
    if manifest['image_archive'] is not local or (directory / 'application-image.tar').is_file() != local:
        raise ValueError('Missing or ambiguous application image archive')
    if hashlib.sha256((directory / 'config.env').read_bytes()).hexdigest() != manifest['config_sha256']:
        raise ValueError('Recovery configuration does not match manifest')
    env = manifest['environment']
    values = manifest['config_values']
    if (set(env) != set(KEYS) or set(values) != {'APP_IMAGE', 'POSTGRES_PASSWORD', *KEYS} - {'DATABASE_URL'}
            or values['APP_IMAGE'] != reference
            or any(values[key] != env[key] for key in KEYS if key != 'DATABASE_URL')
            or unquote(urlsplit(env['DATABASE_URL']).password or '') != values['POSTGRES_PASSWORD']):
        raise ValueError('Ambiguous recovery configuration')
    config = replace_config((target / '.env').read_text(encoding='utf-8'), values)
    (output / 'config.env').write_text(config, encoding='utf-8')
    # Compose interpolates dollar signs even in JSON; quote them explicitly.
    escaped = {key: value.replace('$', '$$') for key, value in env.items()}
    services = {name: {'image': reference, 'pull_policy': 'never', 'environment': escaped}
                for name in (*APPLICATIONS, 'migrate')}
    services['db'] = {'environment': {'POSTGRES_PASSWORD': values['POSTGRES_PASSWORD'].replace('$', '$$')}}
    write_json(output / 'recovery.compose.json', {'services': services})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['capture', 'prepare'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'capture':
            capture(args.directory)
        else:
            prepare(args.directory, args.target, args.output)
    except (KeyError, TypeError, ValueError, OSError):
        # Never echo source JSON, environment values, or exception details containing keys.
        raise SystemExit('Recovery metadata/configuration is missing, inconsistent, or unsupported.') from None


if __name__ == '__main__':
    main()
