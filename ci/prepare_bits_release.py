"""Inspect tested BITS payloads and prepare direct Release attachments in Linux."""
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys

from public_release_audit import inspect_tar, COUNTS, PRIVATE
from release_assets import inventory, prepare, sha


def main():
    if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Cloud Linux only')
    mode, directory = sys.argv[1:]
    root = Path(directory)
    metadata = json.loads((root / 'RELEASE.json').read_text())
    if metadata['source_commit'] != os.environ['GITHUB_SHA']:
        raise ValueError('Release source differs from the tested commit')
    inventory(root)
    if mode == 'system':
        archive = root.parent / (root.name + '.tar.gz')
    elif mode == 'legacy':
        archive = Path('bits-o-' + metadata['version'] + '.tar.gz')
    else:
        raise ValueError('Unknown release mode')
    if Path(str(archive) + '.sha256').read_text().split() != [sha(archive), archive.name]:
        raise ValueError('Release archive checksum mismatch')
    inspect_tar(archive.read_bytes(), archive.name)
    # This branch has clean public ancestry. Never archive private reference history.
    for commit in subprocess.check_output(['git', 'rev-list', 'HEAD']).decode().splitlines():
        names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', commit]).decode().splitlines()
        for name in names:
            if any(part in PRIVATE for part in PurePosixPath(name).parts):
                raise ValueError('Private source in publishable ancestry: ' + name)
    output = root.parent / 'release-assets' if mode == 'system' else root
    if mode == 'system':
        prepare(root, output)
    report = {'status': 'passed', 'source_commit': os.environ['GITHUB_SHA'],
              'workflow_run': os.environ['GITHUB_RUN_ID'], 'mode': mode,
              'archive_sha256': sha(archive), 'scanned': COUNTS,
              'scope': 'recursive payload paths, reviewed source policy, public ancestry and checksums',
              'security_limit': 'not a proof against native reverse engineering or unknown vulnerabilities'}
    (output / 'PUBLICATION-AUDIT.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.name + '\n'
        for p in sorted(output.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
