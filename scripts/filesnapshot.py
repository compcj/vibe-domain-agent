"""File volume snapshot helper. Run as the application's unprivileged user."""
import argparse
import sys
import tarfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['create', 'restore'])
    parser.add_argument('--directory', type=Path, default=Path('/app/data'))
    args = parser.parse_args()
    if args.action == 'create':
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
            archive.add(args.directory, arcname='data')
        return
    destination = args.directory.resolve()
    if any(item.is_file() or item.is_symlink() for item in destination.rglob('*')):
        raise SystemExit('Restore requires an empty application volume.')
    with tarfile.open(fileobj=sys.stdin.buffer, mode='r|gz') as archive:
        for member in archive:
            relative = Path(member.name)
            if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'data':
                raise SystemExit('Invalid snapshot path.')
            if not (member.isfile() or member.isdir()):
                raise SystemExit('Only regular files and directories are permitted.')
            member.name = str(Path(*relative.parts[1:]))
            archive.extract(member, path=destination, filter='data')


if __name__ == '__main__':
    main()
