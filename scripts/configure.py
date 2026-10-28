"""Generate local configuration without printing secrets or overwriting existing files."""
import argparse
import base64
import os
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('.env'))
    parser.add_argument('--development', action='store_true')
    args = parser.parse_args()
    values = {
        'APP_ENV': 'development' if args.development else 'production',
        'APP_IMAGE': 'ghcr.io/compcj/vibe-domain-agent:latest',
        'HTTP_PORT': '8080',
        'HTTPS_PORT': '443',
        'SECRET_KEY': secrets.token_hex(32),
        'ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'POSTGRES_PASSWORD': secrets.token_hex(24),
        'DATABASE_URL': 'sqlite:///./data/app.db',
        'REDIS_URL': 'redis://127.0.0.1:6379/0',
        'STORAGE_DIR': './data/files',
        'FRONTEND_DIST': './frontend/dist',
    }
    try:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit('Configuration already exists; it was not changed.') from None
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write('\n'.join(f'{key}={value}' for key, value in values.items()) + '\n')
    print(f'Configuration created: {args.output}. Keep an encrypted backup of this file.')


if __name__ == '__main__':
    main()
