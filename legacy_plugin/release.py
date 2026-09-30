"""Assemble only matching, completely verified cloud package outputs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from . import VERSION
from distribution.source_export import export_sources
from build import PUBLIC_API_VERSION, PLUGIN_VERSION

MATRIX = {'rocky8', 'rocky9', 'rocky10', 'alma8', 'alma9', 'alma10',
          'debian11', 'debian12', 'debian13', 'ubuntu22', 'ubuntu24', 'ubuntu26'}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Cloud Linux release assembly only')
    inputs = Path('legacy-release-input')
    run = os.environ['GITHUB_RUN_ID']
    package = inputs / ('legacy-packages-' + run)
    checks = {}
    for row in inputs.glob('legacy-test-*'):
        label = row.name[len('legacy-test-'):].rsplit('-', 1)[0]
        value = json.loads((row / 'validation.json').read_text())
        if value.get('status') != 'passed' or value['source_commit'] != os.environ['GITHUB_SHA']:
            raise ValueError('Missing/mismatched successful validation: ' + label)
        value['image'] = (row / 'image.txt').read_text().strip()
        checks[label] = value
    if set(checks) != MATRIX:
        raise ValueError('All twelve distributions must pass before release assembly')
    upgrade = json.loads((inputs / ('legacy-test-rocky8-' + run) / 'field-upgrade/validation.json').read_text())
    if upgrade['status'] != 'passed' or upgrade['source_commit'] != os.environ['GITHUB_SHA'] or upgrade['mode'] != 'field-upgrade':
        raise ValueError('Field-version upgrade path did not pass')
    out = Path('legacy-release')
    out.mkdir(exist_ok=True)
    inventory = {}
    for line in (package / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split()
        if Path(name).name != name or sha(package / name) != checksum:
            raise ValueError('Package output differs')
        shutil.copyfile(str(package / name), str(out / name))
        inventory[name] = {'sha256': checksum, 'bytes': (out / name).stat().st_size}
    (out / 'VALIDATION.json').write_text(json.dumps({'source_commit': os.environ['GITHUB_SHA'],
        'workflow_run': run, 'matrix': checks, 'field_version_upgrade': upgrade,
        'hardware_validation': 'pending on real 215/K6C-165', 'kernel': 'shared cloud host kernel',
        'storage': 'isolated local directory served over real rsync; production NFS mapping unchanged',
        'scope': 'native packages, isolated real Redis/old scheduler/workloads/delivery; synthetic sensors'},
        ensure_ascii=False, indent=2) + '\n')
    source_export = export_sources(out / 'ocrun-plugin-source.tar.gz', os.environ['GITHUB_SHA'])
    (out / 'RELEASE.json').write_text(json.dumps({'name': 'ocrun-plugin', 'version': VERSION,
        'customer_sources': source_export,
        'component_versions': {'sckocp-api': PUBLIC_API_VERSION, 'mon-sensors-plugin': PLUGIN_VERSION},
        'source_commit': os.environ['GITHUB_SHA'], 'workflow_run': run, 'files': inventory,
        'roles': ['control', 'node'], 'task_protocol': 'original OCRUN per-host Redis layout',
        'production_deployment': 'not performed', 'baseline_tools_release': 'v0.2.2',
        'baseline_tools_sha256': 'de52917ac365a27cc092e74755da9d8c4d81e1c6ac85d16bd73882db81372949'}, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.name + '\n' for p in sorted(out.iterdir())
        if p.is_file() and p.name != 'SHA256SUMS'))
    archive = Path('ocrun-plugin-' + VERSION + '.tar.gz')
    with tarfile.open(str(archive), 'w:gz') as bundle:
        bundle.add(str(out), arcname='ocrun-plugin-' + VERSION)
    Path(str(archive) + '.sha256').write_text(sha(archive) + '  ' + archive.name + '\n')


if __name__ == '__main__':
    main()
