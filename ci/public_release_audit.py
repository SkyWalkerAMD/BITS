"""Read-only publication audit, executed only in private cloud Linux."""
import hashlib
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

from release_assets import prepare, sha, inventory

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.public-audit'
SEEN = set()
COUNTS = {'archives': 0, 'members': 0, 'native_packages': 0, 'installers': 0}
PRIVATE = ('research', 'sckocp120', 'sckocp-security', 'activation', 'drafts', '.git')


def inspect_tar(data, label, depth=0):
    digest = hashlib.sha256(data).hexdigest()
    if digest in SEEN:
        return
    SEEN.add(digest)
    if depth > 12:
        raise ValueError('Excessive nested archive: ' + label)
    COUNTS['archives'] += 1
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith('/') or '..' in parts:
                raise ValueError('Unsafe path: ' + label + ':' + member.name)
            if any(part in PRIVATE for part in parts):
                raise ValueError('Private source: ' + label + ':' + member.name)
            if 'integrations' in parts and any(p.startswith('sckocp') for p in parts):
                raise ValueError('Native reference: ' + label + ':' + member.name)
            if member.name.endswith(('SCKOCP-NATIVE-SECURITY.md', 'SCKOCP-SERVER-MIGRATION.md')):
                raise ValueError('Private research document: ' + label + ':' + member.name)
            COUNTS['members'] += 1
            if not member.isfile():
                continue
            if member.size > 256 * 1024 * 1024:
                raise ValueError('Unexpected oversized member')
            payload = None
            if member.name.endswith(('.tar.gz', '.run', '.rpm', '.deb')):
                payload = archive.extractfile(member).read()
            if member.name.endswith('.tar.gz'):
                inspect_tar(payload, label + ':' + member.name, depth + 1)
            elif member.name.endswith('.run'):
                COUNTS['installers'] += 1
                offset = payload.find(b'\x1f\x8b\x08')
                if offset < 0:
                    raise ValueError('Installer has no inspectable payload')
                inspect_tar(payload[offset:], label + ':' + member.name, depth + 1)
            elif member.name.endswith(('.rpm', '.deb')):
                inspect_native(payload, member.name, depth + 1)


def inspect_native(data, name, depth):
    digest = hashlib.sha256(data).hexdigest()
    if digest in SEEN:
        return
    SEEN.add(digest)
    COUNTS['native_packages'] += 1
    with tempfile.TemporaryDirectory() as tmp:
        package = Path(tmp) / Path(name).name
        package.write_bytes(data)
        if name.endswith('.deb'):
            result = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(package)])
            inspect_tar(result, name, depth)
        else:
            raw = subprocess.check_output(['rpm2cpio', str(package)])
            names = subprocess.check_output(['cpio', '-it', '--quiet'], input=raw).decode().splitlines()
            if any('..' in PurePosixPath(n).parts or n.startswith('/') or
                   any(p in PRIVATE for p in PurePosixPath(n).parts) for n in names):
                raise ValueError('Unsafe native payload path')
            target = Path(tmp) / 'payload'
            target.mkdir()
            subprocess.run(['cpio', '-idmu', '--quiet', '--no-absolute-filenames'],
                           input=raw, cwd=str(target), check=True)
            for path in target.rglob('*'):
                if path.is_symlink():
                    continue
                if path.is_file() and path.name.endswith(('.tar.gz', '.run', '.rpm', '.deb')):
                    value = path.read_bytes()
                    if path.name.endswith('.tar.gz'):
                        inspect_tar(value, path.name, depth + 1)
                    elif path.name.endswith('.run'):
                        offset = value.find(b'\x1f\x8b\x08')
                        if offset < 0:
                            raise ValueError('Installer payload missing')
                        inspect_tar(value[offset:], path.name, depth + 1)
                    else:
                        inspect_native(value, path.name, depth + 1)


def extract_regular(archive, destination):
    destination.mkdir()
    with tarfile.open(str(archive)) as bundle:
        for member in bundle:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith('/') or '..' in parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unexpected release archive entry')
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.extractfile(member).read())


def main():
    if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Authorized cloud Linux only')
    OUT.mkdir(exist_ok=True)
    archives = {'ocrun-system-0.2.3.tar.gz': '9ed9b170b435445831fcfbf01b73edb3ddb528462c491b09b14cb00d564b370d',
                'ocrun-plugin-0.1.1.tar.gz': '53c9ac3a86473dd1ca76160f086d87708787bb36a7a09b70fe4c337d284233bb'}
    for name, digest in archives.items():
        path = ROOT / 'audit-input' / name
        if sha(path) != digest:
            raise ValueError('Original release checksum mismatch: ' + name)
        inspect_tar(path.read_bytes(), name)
    root = ROOT / 'audit-input/ocrun-system-0.2.3'
    with tarfile.open(str(ROOT / 'audit-input/ocrun-system-0.2.3.tar.gz')) as bundle:
        # Traversal and type checks already performed, extract only regular files.
        for member in bundle:
            if member.isfile():
                path = ROOT / 'audit-input' / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bundle.extractfile(member).read())
    additions = prepare(root, ROOT / 'audit-assets')
    # Native MLC license is inspected, not executed.
    with tarfile.open(str(ROOT / 'workload_suite/vendor/mlc.tar.gz')) as bundle:
        pdf = OUT / 'MLC-LICENSE.pdf'
        license_bytes = bundle.extractfile('Intel Memory Latency Tools Outbound License Agreement.pdf').read()
        # Intel's pinned archive stores this .pdf member gzip-compressed.
        if license_bytes.startswith(b'\x1f\x8b'):
            license_bytes = gzip.decompress(license_bytes)
        pdf.write_bytes(license_bytes)
        (OUT / 'MLC-REDIST.txt').write_bytes(bundle.extractfile('Linux/redist.txt').read())
    subprocess.run(['pdftotext', '-layout', str(pdf), str(OUT / 'MLC-LICENSE.txt')], check=True)
    tracked = subprocess.check_output(['git', 'ls-files', '-z']).decode().rstrip('\0').split('\0')
    for name in tracked:
        if any(part in PRIVATE for part in PurePosixPath(name).parts):
            raise ValueError('Private current file: ' + name)
    for commit in subprocess.check_output(['git', 'rev-list', 'HEAD']).decode().splitlines():
        names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', commit]).decode().splitlines()
        if any(any(part in PRIVATE for part in PurePosixPath(name).parts) for name in names):
            raise ValueError('Private parent commit: ' + commit)
    report = {'status': 'passed', 'source_commit': os.environ['GITHUB_SHA'], 'release_bytes_unchanged': True,
              'source_and_history_private_paths': 'absent', 'counts': COUNTS,
              'checked_release_archives': archives, 'attachments': additions,
              'scope': 'source/history paths and recursive package inventory; not an anti-reverse-engineering guarantee'}
    (OUT / 'AUDIT.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'attachments'}, indent=2))


if __name__ == '__main__':
    main()
