"""Exercise Docker's real ignore rules using only a disposable synthetic context."""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_export(context, output, controls):
    subprocess.run(
        ["docker", "build", "--output", f"type=local,dest={output}", str(context)],
        check=True,
    )
    actual = {path.relative_to(output) for path in output.rglob("*") if path.is_file()}
    assert actual == set(controls), f"Unexpected build context contents: {actual ^ set(controls)}"


def check(ignore_file=ROOT / ".dockerignore"):
    private_names = [
        "client.pfx",
        "client.pem",
        "client.key",
        "local.db",
        "local.db-wal",
        "local.db-shm",
        "local.sqlite3",
        ".env",
        ".env.production",
        "service.log",
        "storage/document.txt",
        "data/checkpoint",
        "uploads/document.txt",
        "logs/service.txt",
        "tls/certificate",
        "backups/snapshot",
        ".git/config",
        ".worktrees/private/document",
        ".cache/private",
        ".venv/private",
        ".superpowers/private",
        ".tools/private",
        ".pytest-tmp-private/document",
        ".vda-backup.test/snapshot",
        "vda-20261028T010203Z.tar.gz",
    ]
    # Never place sentinels in the real checkout or copy its contents into the build.
    with tempfile.TemporaryDirectory(prefix="vda-context-") as temporary:
        root = Path(temporary)
        context = root / "context"
        context.mkdir()
        controls = []
        for directory in ("backend", "scripts", "frontend", "backend/nested"):
            public = context / directory / "public-control.txt"
            public.parent.mkdir(parents=True, exist_ok=True)
            public.write_text("synthetic public control", encoding="utf-8")
            controls.append(public.relative_to(context))
            for name in private_names:
                path = context / directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic excluded sentinel", encoding="utf-8")
        (context / "Dockerfile").write_text(
            "FROM scratch\nCOPY backend/ /backend/\nCOPY scripts/ /scripts/\n"
            "COPY frontend/ /frontend/\n",
            encoding="utf-8",
        )
        # Prove the same detector rejects inclusion, using an empty ignore file.
        # Build failures are not swallowed: only the content assertion is expected.
        (context / ".dockerignore").write_text("", encoding="utf-8")
        try:
            verify_export(context, root / "negative", controls)
        except AssertionError:
            print("Negative control detected private synthetic files in the build output.")
        else:
            raise AssertionError("Negative control did not detect included private files")
        shutil.copyfile(ignore_file, context / ".dockerignore")
        verify_export(context, root / "output", controls)
    print("Docker build context excludes private sentinels and preserves public controls.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ignore-file", type=Path, default=ROOT / ".dockerignore")
    check(parser.parse_args().ignore_file)
