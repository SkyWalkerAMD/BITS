"""Assemble cloud evidence and already-tested binaries; never build on Windows."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

from . import VERSION
from .platforms import MATRIX


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_metadata(run_id):
    if not run_id.isdigit():
        raise ValueError('Expected numeric workflow run ID')
    url = 'https://api.github.com/repos/{}/actions/runs/{}'.format(os.environ['GITHUB_REPOSITORY'], run_id)
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                                                  'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Authorized Linux cloud packaging only')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
    ids = {name: os.environ[name.upper() + '_RUN'] for name in ('matrix', 'vm', 'node')}
    runs = {name: run_metadata(value) for name, value in ids.items()}
    for name, run in runs.items():
        if run['status'] != 'completed' or (name in ('vm', 'node') and run['conclusion'] != 'success'):
            raise ValueError('Required validation has not completed successfully: ' + name)
        if name in ('matrix', 'vm') and run['head_sha'] != commit:
            raise ValueError('Validation and server release source differ: ' + name)
    destination = Path('server-release/ocrun-server-deployment-' + VERSION)
    destination.mkdir(parents=True)
    evidence = destination / 'validation'
    evidence.mkdir()
    package_dir = Path('release-input/matrix/server-package-' + ids['matrix'])
    package = package_dir / ('ocrun-server-' + VERSION + '.tar.gz')
    expected = (package_dir / 'SHA256SUMS').read_text().split()[0]
    if checksum(package) != expected:
        raise ValueError('Tested server archive checksum differs')
    with tarfile.open(str(package)) as archive:
        manifest = json.load(archive.extractfile('ocrun-server-' + VERSION + '/PACKAGE.json'))
        if manifest['source_commit'] != commit:
            raise ValueError('Server package source does not match validation')
    shutil.copyfile(str(package), str(destination / package.name))
    platforms = []
    for label, image in MATRIX:
        folder = Path('release-input/matrix/server-' + label + '-' + ids['matrix'])
        value_file = folder / 'server.json'
        if value_file.exists():
            value = json.loads(value_file.read_text())
            if value['status'] != 'passed':
                raise ValueError('Server service validation failed: ' + label)
            platforms.append(dict(label=label, image=image, result=value))
        elif (label == 'debian11' and (folder / 'base-image.txt').exists()
              and (folder / 'packages.txt').exists()
              and 'Failed to fetch' in (folder / 'packages.txt').read_text()
              and '404' in (folder / 'packages.txt').read_text()
              and 'debian-security' in (folder / 'packages.txt').read_text()):
            platforms.append(dict(label=label, image=image, result={'status': 'blocked',
                'reason': 'Debian bullseye-security indexes reference absent packages (HTTP 404)',
                'upstream': 'https://bugs.debian.org/1147093', 'deployment_passed': False}))
        else:
            raise ValueError('Missing required OS validation: ' + label)
        shutil.copytree(str(folder), str(evidence / label))
    native = []
    for label in ('rocky8', 'rocky10', 'ubuntu26'):
        folder = Path('release-input/vm/server-vm-' + label + '-' + ids['vm'])
        if (folder / 'selinux-trace.txt').exists():
            raise ValueError('Diagnostic policy run cannot serve as release acceptance: ' + label)
        value = json.loads((folder / 'server.json').read_text())
        health = json.loads((folder / 'reboot-check.json').read_text())
        boot_ids = dict(line.split('=', 1) for line in (folder / 'boot-ids.txt').read_text().splitlines())
        if value['status'] != 'passed' or health['status'] != 'ok' or boot_ids['before'] == boot_ids['after']:
            raise ValueError('Missing native service/reboot success: ' + label)
        if label.startswith('rocky') and not value['native_selinux_enforcing']:
            raise ValueError('Native EL test did not keep SELinux enforcing')
        native.append(dict(label=label, result=value, reboot_verified=True))
        shutil.copytree(str(folder), str(evidence / ('vm-' + label)))
    node_archives = list(Path('release-input/node').rglob('mon-sensors-finish-auth-0.2.2.tar.gz'))
    if len(node_archives) != 1:
        raise ValueError('Expected exactly one authenticated node archive')
    node_archive = node_archives[0]
    if checksum(node_archive) != Path(str(node_archive) + '.sha256').read_text().split()[0]:
        raise ValueError('Node archive checksum differs')
    with tarfile.open(str(node_archive)) as archive:
        node_release = json.load(archive.extractfile('mon-sensors-finish-auth-0.2.2/RELEASE.json'))
        if node_release['source_commit'] != runs['node']['head_sha']:
            raise ValueError('Node archive provenance differs')
    shutil.copyfile(str(node_archive), str(destination / node_archive.name))
    for name in ('MANUAL.md', 'ACCEPTANCE.md'):
        shutil.copyfile('server_deploy/' + name, str(destination / name))
    release = {'version': VERSION, 'component': 'ocrun-server-deployment', 'source_commit': commit,
               'protocol': 'ocrun-legacy-v1 with authenticated new-center connections',
               'automatic_task_polling': False, 'existing_production_changes': False,
               'platforms': platforms, 'native_virtual_machines': native,
               'workflow_runs': {name: {'id': ids[name], 'url': run['html_url'], 'commit': run['head_sha'],
                                        'conclusion': run['conclusion']} for name, run in runs.items()},
               'node_component': node_release,
               'field_acceptance': 'pending on a new center and one explicitly selected idle node',
               'limits': ['Debian 11 repository failure blocks complete deployment verification',
                          'RHEL subscriptions, other EL distributions and non-x86_64 not validated',
                          'Existing node program/tool baseline required; no PXE or complete legacy binary bundle',
                          'Hardware sensors, large-scale concurrent nodes and production storage require field acceptance']}
    (destination / 'RELEASE.json').write_text(json.dumps(release, ensure_ascii=False, indent=2) + '\n')
    (destination / 'SHA256SUMS').write_text(''.join(checksum(path) + '  ' + path.relative_to(destination).as_posix() + '\n'
        for path in sorted(destination.rglob('*')) if path.is_file()))
    output = destination.parent / (destination.name + '.tar.gz')
    with tarfile.open(str(output), 'w:gz') as archive:
        archive.add(str(destination), arcname=destination.name)
    Path(str(output) + '.sha256').write_text(checksum(output) + '  ' + output.name + '\n')
    print(json.dumps({'archive': str(output), 'sha256': checksum(output), 'source_commit': commit}))


if __name__ == '__main__':
    main()
