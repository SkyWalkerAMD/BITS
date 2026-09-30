"""Collect tested cloud artifacts; no rebuild and no Windows execution."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def run_info(run_id):
    if not run_id.isdigit():
        raise ValueError('Numeric run ID required')
    url = 'https://api.github.com/repos/{}/actions/runs/{}'.format(os.environ['GITHUB_REPOSITORY'], run_id)
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN']})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(source), str(target))


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Authorized cloud Linux only')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
    ids = {n: os.environ[n.upper() + '_RUN'] for n in ('workloads', 'server')}
    runs = {n: run_info(v) for n, v in ids.items()}
    for name, run in runs.items():
        if run['conclusion'] != 'success' or run['head_sha'] != commit:
            raise ValueError('Required successful validation must match release commit: ' + name)
    target = Path('workload-release/ocrun-toolkit-0.1.2')
    target.mkdir(parents=True)
    workload = Path('release-input/workloads')
    matrix = []
    from server_deploy.platforms import MATRIX
    for label, image in MATRIX:
        source = workload / ('workload-test-' + label + '-' + ids['workloads'])
        result = json.loads((source / 'tests.json').read_text())
        if (not result['passed'] or result['source_commit'] != commit or result['tests'] < 20
                or 'mlc_version' not in result['details']):
            raise ValueError('Missing workload execution acceptance: ' + label)
        for name in ('tests.json', 'test.txt', 'image.json', 'uninstall.txt', 'tamper-reinstall.txt'):
            copy(source / name, target / 'validation/workloads' / label / name)
        center = Path('release-input/server/server-' + label + '-' + ids['server'])
        services = json.loads((center / 'server.json').read_text())
        if services['status'] != 'passed':
            raise ValueError('Missing server service acceptance: ' + label)
        for name in ('server.json', 'services.txt', 'base-image.txt', 'network.txt'):
            copy(center / name, target / 'validation/server' / label / name)
        matrix.append({'os': label, 'image': image, 'package_tests': result['tests'],
                       'tools': 'passed', 'center_systemd_services': 'passed',
                       'kernel': 'container shares cloud host kernel'})
    packages = {}
    for kind, filename in [('rpm', 'ocrun-workloads-0.1.0-3.el8.x86_64.rpm'),
                           ('deb', 'ocrun-workloads_0.1.0-3_amd64.deb')]:
        source = workload / ('workload-' + kind + '-' + ids['workloads'])
        inventory = json.loads((source / 'WORKLOADS.json').read_text())
        if inventory['source_commit'] != commit:
            raise ValueError('Built payload source differs')
        expected = dict((line.split()[1], line.split()[0]) for line in (source / 'SHA256SUMS').read_text().splitlines())
        if sha(source / filename) != expected[filename]:
            raise ValueError('Package checksum differs')
        copy(source / filename, target / filename)
        copy(source / 'WORKLOADS.json', target / ('WORKLOADS-' + kind + '.json'))
        for name in ('build.txt', 'build-image.txt', 'dependencies.txt'):
            copy(source / name, target / 'validation/build' / kind / name)
        packages[kind] = {'filename': filename, 'sha256': expected[filename]}
    bridge = workload / ('workload-bridge-' + ids['workloads'])
    for relative in ('finish-dist/mon-sensors-finish-0.2.5.run',
                     'dist/mon-sensors-plugin-0.12.10.run', 'dist/sckocp-api-0.3.1.run'):
        copy(bridge / relative, target / Path(relative).name)
    regression = workload / ('workload-regression-' + ids['workloads'])
    for name in ('finish-addon.json', 'upgrade.json'):
        value = json.loads((regression / name).read_text())
        if value['status'] != 'passed':
            raise ValueError('Node regression failed: ' + name)
        copy(regression / name, target / 'validation/node' / name)
    copy(regression / 'mon-sensors-report-py36-0.2.0.run', target / 'mon-sensors-report-py36-0.2.0.run')
    copy(regression / 'mon-sensors-plugin-0.12.9.tar.gz', target / 'rollback/mon-sensors-plugin-0.12.9.tar.gz')
    center = Path('release-input/server/server-package-' + ids['server'])
    copy(center / 'ocrun-server-0.1.3.tar.gz', target / 'ocrun-server-0.1.3.tar.gz')
    # Ship corresponding pinned sources and build recipes with the native tools.
    inputs = workload / ('workload-inputs-' + ids['workloads'])
    sources = json.loads((inputs / 'sources.json').read_text())
    for name, data in sources['sources'].items():
        if name == 'cpu2017':
            continue
        path = inputs / (name + '.tar.gz')
        if sha(path) != data['sha256']:
            raise ValueError('Upstream source archive differs: ' + name)
        copy(path, target / 'sources' / path.name)
    copy(inputs / 'sources.json', target / 'SOURCES.json')
    subprocess.run(['git', 'archive', '--format=tar.gz', '--output=' + str(target / 'sources/ocrun-integration-source.tar.gz'),
                    'HEAD'], check=True)
    for name in ('MANUAL.md', 'ACCEPTANCE.md'):
        copy(Path('workload_suite') / name, target / name)
    copy(Path('server_deploy/MANUAL.md'), target / 'SERVER-MANUAL.md')
    # Exact current signed-index dependency bytes make the known Debian 11 pool
    # outage reviewable. These are OS-specific and must not be used on other OSes.
    debian = workload / ('workload-test-debian11-' + ids['workloads'])
    if (debian / 'debian11-cache').exists():
        for dependency in (debian / 'debian11-cache').glob('*.deb'):
            copy(dependency, target / 'debian11-dependencies' / dependency.name)
        copy(debian / 'debian11-dependency-receipt.json', target / 'debian11-dependencies/PROVENANCE.json')
    metadata = {'version': '0.1.2', 'workload_version': '0.1.0', 'package_revision': '3', 'source_commit': commit, 'packages': packages, 'matrix': matrix,
                'workflow_runs': {name: {'id': ids[name], 'url': run['html_url']} for name, run in runs.items()},
                'unchanged_native_api': '0.3.1', 'plugin': '0.12.10', 'finish': '0.2.5', 'report': '0.2.0',
                'new_center': '0.1.3', 'production_changes': False, 'automatic_task_polling': False,
                'field_validation_pending': ['MLC 3.13 full hardware run', 'original SPEC 1.0.5 import and full run',
                    'cyclictest FIFO real-time scheduling and latency', 'P95 AVX/FMA3/AVX512 and prolonged hardware load',
                    'all distro-native kernels and full new-center plus hardware-node deployment'],
                'package_signing': 'SHA-256 release inventory; no distribution signing key configured'}
    (target / 'RELEASE.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    (target / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.relative_to(target).as_posix() + '\n'
        for p in sorted(target.rglob('*')) if p.is_file()))
    archive = target.parent / (target.name + '.tar.gz')
    with tarfile.open(str(archive), 'w:gz') as bundle:
        bundle.add(str(target), arcname=target.name)
    Path(str(archive) + '.sha256').write_text(sha(archive) + '  ' + archive.name + '\n')
    print(json.dumps({'archive': str(archive), 'sha256': sha(archive), 'commit': commit}))


if __name__ == '__main__':
    main()
