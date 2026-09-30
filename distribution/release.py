"""Collect this run's already-built, successfully tested native packages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from distribution.build import sha, copy, put, check_input, VERSION
from server_deploy.platforms import MATRIX
from distribution.source_export import export_sources
from build import PUBLIC_API_VERSION, PLUGIN_VERSION


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Authorized cloud Linux only')
    commit, run = os.environ['GITHUB_SHA'], os.environ['GITHUB_RUN_ID']
    component_run = os.environ.get('COMPONENT_RUN') or run
    component_commit = os.environ.get('COMPONENT_COMMIT') or commit
    inputs = Path('release-input')
    target = Path('system-dist') / ('ocrun-system-' + VERSION)
    target.mkdir(parents=True)
    matrix = []
    for label, image in MATRIX:
        native = inputs / ('native-test-' + label + '-' + run)
        evidence = json.loads((native / 'native.json').read_text())
        tools = inputs / ('workload-test-' + label + '-' + component_run)
        t = json.loads((tools / 'tests.json').read_text())
        center = inputs / ('server-' + label + '-' + component_run)
        c = json.loads((center / 'server.json').read_text())
        if (evidence['status'] != 'passed' or evidence['source_commit'] != commit
                or not t['passed'] or t['source_commit'] != component_commit or t['tests'] < 20
                or 'mlc_version' not in t['details'] or c['status'] != 'passed'):
            raise ValueError('Incomplete successful validation for ' + label)
        for directory, names, category in ((native, ['native.json', 'test.txt', 'image.txt'], 'native'),
                (tools, ['tests.json', 'test.txt', 'image.json'], 'tools'),
                (center, ['server.json', 'services.txt', 'base-image.txt', 'network.txt'], 'center')):
            for name in names:
                copy(directory / name, target / 'validation' / category / label / name)
        matrix.append({'os': label, 'image': image, 'native_install_batch_delivery_removal': 'passed',
                       'tool_tests': t['tests'], 'center_systemd': 'passed', 'kernel': 'shared cloud host kernel'})
    packages = {}
    for role in ('node', 'center'):
        for kind in ('rpm', 'deb'):
            source = inputs / ('native-' + role + '-' + kind + '-' + run)
            choices = list(source.glob('*.' + kind))
            if len(choices) != 1:
                raise ValueError('Expected one native package')
            package = choices[0]
            check_input(package)
            copy(package, target / package.name)
            packages[role + '-' + kind] = {'file': package.name, 'bytes': package.stat().st_size, 'sha256': sha(package)}
    bridge = inputs / ('workload-bridge-' + component_run)
    for relative in ('dist/sckocp-api-' + PUBLIC_API_VERSION + '.run',
                     'dist/mon-sensors-plugin-' + PLUGIN_VERSION + '.run',
                     'finish-dist/mon-sensors-finish-0.2.5.run'):
        copy(bridge / relative, target / 'standalone' / Path(relative).name)
    regression = inputs / ('workload-regression-' + component_run)
    copy(regression / 'mon-sensors-report-py36-0.2.0.run', target / 'standalone/mon-sensors-report-py36-0.2.0.run')
    for name in ('finish-addon.json', 'upgrade.json'):
        if json.loads((regression / name).read_text())['status'] != 'passed':
            raise ValueError('Node recovery regression failed')
        copy(regression / name, target / 'validation' / name)
    visual = json.loads((regression / 'report-visual.json').read_text())
    if visual.get('status') != 'passed':
        raise ValueError('Report visual checks did not pass')
    for name in ('report-preview.html', 'report-preview.json', 'report-preview.png', 'report-print-preview.pdf', 'report-visual.json'):
        copy(regression / name, target / 'example' / name)
    control = inputs / ('mon-sensors-control-0.2.0-' + run)
    if json.loads((control / 'RELEASE.json').read_text())['source_commit'] != commit:
        raise ValueError('Controller reader has different source')
    copy(control / 'mon-sensors-control-0.2.0.run', target / 'standalone/mon-sensors-control-0.2.0.run')
    copy(control / 'RELEASE.json', target / 'validation/control-reader.json')
    for kind in ('rpm', 'deb'):
        source = inputs / ('workload-' + kind + '-' + component_run)
        for package in source.glob('*.' + kind):
            check_input(package)
            copy(package, target / 'standalone' / package.name)
    source = inputs / ('workload-inputs-' + component_run)
    sources = json.loads((source / 'sources.json').read_text())
    for tool, entry in sources['sources'].items():
        if tool == 'cpu2017':
            continue
        archive = source / (tool + '.tar.gz')
        if sha(archive) != entry['sha256']:
            raise ValueError('Upstream source differs')
        copy(archive, target / 'sources' / archive.name)
    copy(source / 'sources.json', target / 'SOURCES.json')
    source_export = export_sources(target / 'sources/ocrun-source.tar.gz', commit)
    documents = dict([('docs/deployment/DISTRIBUTION.md', 'README.md'), ('docs/workloads/MANUAL.md', 'WORKLOADS-MANUAL.md'),
                           ('docs/deployment/SERVER.md', 'SERVER-MANUAL.md'), ('docs/monitoring/SCKOCP-API.md', 'SCKOCP-API.md'),
                           ('docs/reports/ACCEPTANCE-REPORT.md', 'ACCEPTANCE-REPORT.md'), ('docs/plugins/CONTROL-READER.md', 'CONTROL-MANUAL.md'),
                           ('docs/releases/0.2.3.md', 'OPTIMIZATION.md')])
    from build import document_bytes
    for original, name in documents.items():
        put(target / name, document_bytes(original, name, documents))
    metadata = {'version': VERSION, 'source_commit': commit, 'packages': packages, 'matrix': matrix,
                'customer_sources': source_export,
                'component_versions': {'sckocp-api': PUBLIC_API_VERSION, 'mon-sensors-plugin': PLUGIN_VERSION},
                'component_commit': component_commit, 'component_run': component_run,
                'workflow': 'https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '/actions/runs/' + run,
                'scope': 'new native center and new native node with included selected workload tools',
                'monitor_fixture': 'synthetic; no claim of licensed hardware sensor validation in CI',
                'production_changed': False, 'signing': 'SHA-256; no RPM/GPG signing key configured',
                'field_pending': ['real sckocp hardware collection on new native node', 'native OS kernel/SELinux enforcement',
                                  'MLC full hardware workloads and original SPEC import/full workloads', 'P95 CPU modes, sustained load and cyclictest realtime latency']}
    put(target / 'RELEASE.json', json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    put(target / 'SHA256SUMS', ''.join(sha(p) + '  ' + p.relative_to(target).as_posix() + '\n'
        for p in sorted(target.rglob('*')) if p.is_file()))
    archive = target.parent / (target.name + '.tar.gz')
    with tarfile.open(str(archive), 'w:gz') as bundle:
        bundle.add(str(target), arcname=target.name)
    put(Path(str(archive) + '.sha256'), sha(archive) + '  ' + archive.name + '\n')
    print(json.dumps({'archive': str(archive), 'sha256': sha(archive), 'commit': commit}))


if __name__ == '__main__':
    main()
