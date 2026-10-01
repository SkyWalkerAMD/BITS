"""Build legacy system enhancement packages only in cloud Linux."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from . import VERSION
from distribution.build import put, copy, launch, extract_own, sha, check_input

SOURCE = Path(__file__).resolve().parents[1]
PREFIX = '/opt/ocrun-plugin/' + VERSION
COMMAND = '/usr/bin/bits-o'
BASELINE_SHA = 'de52917ac365a27cc092e74755da9d8c4d81e1c6ac85d16bd73882db81372949'
BASELINE_COMMIT = '51d59acaa85ab39fd7b16f8c06c6bf43977f1f43'


def guard(role, kind, remove=False):
    # Both native formats refuse replacement/removal while attached. We never
    # invoke an uninstall script implicitly in a package-manager transaction.
    text = '#!/bin/sh\nset -eu\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\nexport PATH\n'
    text += '''
if [ -e /etc/ocrun-plugin/config.json ]; then
 echo 'Keep the configuration and attachments: explicitly roll back and unconfigure before package removal/replacement.' >&2
 exit 1
fi
'''
    if not remove:
        text += """
if [ -e /opt/ocrun-plugin/0.1.0 ] || [ -L /opt/ocrun-plugin/0.1.0 ] || [ -e CURRENT_PREFIX ] || [ -L CURRENT_PREFIX ] || [ -e /usr/bin/bits-o ] || [ -L /usr/bin/bits-o ]; then
 echo 'Existing plugin paths retained. Remove the detached package before reinstalling.' >&2
 exit 1
fi
"""
    else:
        if kind == 'rpm':
            text += 'rpm -V bits-o-' + role + ' || { echo "Modified package retained." >&2; exit 1; }\n'
        else:
            text += ('differences=$(dpkg --verify bits-o-' + role + ')\n'
                     '[ -z "$differences" ] || { printf "%s\\n" "$differences" >&2; exit 1; }\n')
        text += '''
unsafe=$(find CURRENT_PREFIX /usr/bin/bits-o -xdev \\( ! -user root -o -perm /7022 -o -type l -o -links +1 -type f \\) -print)
[ -z "$unsafe" ] || { echo 'Unsafe package paths retained.' >&2; exit 1; }
'''
    return text.replace('CURRENT_PREFIX', PREFIX)


def package(stage, role, kind, out):
    name = 'bits-o-' + role
    if kind == 'rpm':
        top = stage.parent / ('rpm-' + role)
        for d in ('BUILD', 'BUILDROOT', 'RPMS', 'SRPMS', 'SOURCES', 'SPECS'):
            (top / d).mkdir(parents=True)
        depends = 'bash, findutils, (python3 >= 3.6 or platform-python >= 3.6)'
        if role == 'node':
            depends += ', bits-o-workloads = 0.1.0-4.el8, rsync, curl, util-linux, procps-ng, (redis or valkey), iputils, dmidecode'
        spec = top / 'SPECS/plugin.spec'
        spec.write_text('Name: ' + name + '\nVersion: ' + VERSION + '\nRelease: 1.el8\n'
            'Summary: Original OCRUN system enhancement (' + role + ')\nLicense: GPLv3+\nBuildArch: x86_64\n'
            'Requires: ' + depends + '\nConflicts: bits-o-' + ('node' if role == 'control' else 'control') + ', bits-node, bits-center, ocrun-node, ocrun-center, ocrun-plugin-node, ocrun-plugin-control\n'
            '%global debug_package %{nil}\n%global __os_install_post %{nil}\n%description\n'
            'Explicit legacy integration; no task, network service or scheduler is started by installation.\n'
            '%install\nmkdir -p %{buildroot}\ncp -a ' + str(stage) + '/. %{buildroot}/\n'
            '%pre\n' + guard(role, kind).split('\n', 1)[1] + '\n%preun\n' + guard(role, kind, True).split('\n', 1)[1] +
            '\n%files\n%defattr(-,root,root,-)\n' + PREFIX + '\n' + COMMAND + '\n')
        subprocess.run(['rpmbuild', '-bb', '--define', '_topdir ' + str(top), str(spec)], check=True)
        for p in top.rglob('*.rpm'):
            copy(p, out / p.name)
    else:
        control = stage / 'DEBIAN'
        control.mkdir()
        depends = 'bash, findutils, python3 (>= 3.6)'
        if role == 'node':
            depends += ', bits-o-workloads (= 0.1.0-4), rsync, curl, util-linux, procps, redis-tools, iputils-ping, dmidecode'
        put(control / 'control', 'Package: ' + name + '\nVersion: ' + VERSION + '-1\nArchitecture: amd64\n'
            'Maintainer: OCRUN local deployment\nSection: admin\nPriority: optional\nDepends: ' + depends + '\n'
            'Conflicts: bits-o-' + ('node' if role == 'control' else 'control') + ', bits-node, bits-center, ocrun-node, ocrun-center, ocrun-plugin-node, ocrun-plugin-control\n'
            'Description: Original OCRUN system enhancement\n Explicit attach; installation does not start tasks or services.\n')
        put(control / 'preinst', guard(role, kind), True)
        put(control / 'prerm', guard(role, kind, True), True)
        put(control / 'md5sums', ''.join(hashlib.md5(p.read_bytes()).hexdigest() + '  ' + p.relative_to(stage).as_posix() + '\n'
            for p in sorted(stage.rglob('*')) if p.is_file() and control not in p.parents))
        subprocess.run(['dpkg-deb', '-Zxz', '--uniform-compression', '--build', '--root-owner-group', str(stage),
                        str(out / (name + '_' + VERSION + '-1_amd64.deb'))], check=True)


def build(baseline, output, tools):
    if sha(baseline) != BASELINE_SHA:
        raise ValueError('Published v0.2.2 baseline archive differs')
    output.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(prefix='ocrun-legacy-build-') as temporary:
        tmp = Path(temporary)
        extract_own(baseline, tmp / 'baseline')
        previous = tmp / 'baseline/ocrun-system-0.2.2'
        if json.loads((previous / 'RELEASE.json').read_text())['source_commit'] != BASELINE_COMMIT:
            raise ValueError('Unexpected baseline source revision')
        # Pure-Python Excel writer comes from the already tested native DEB.
        subprocess.run(['dpkg-deb', '-x', str(previous / 'ocrun-node_0.2.2-1_amd64.deb'), str(tmp / 'native')], check=True)
        native = tmp / 'native/opt/ocrun-node/0.2.2'
        subprocess.run([sys.executable, str(SOURCE / 'bits_core/batch/build.py')], check=True)
        import build as component_builder
        component_builder.mon_sensors_package(tmp / 'collector.tar.gz')
        for role in ('control', 'node'):
            for kind in ('rpm', 'deb'):
                stage = tmp / (role + '-' + kind) / 'root'
                root = stage / PREFIX.lstrip('/')
                for module, names in (
                    ('legacy_plugin', ('__init__.py', 'common.py', 'cli.py', 'tasks.py', 'node.py', 'attach.py')),
                    ('distribution', ('common.py',)),
                    ('bits_core/center', ('__init__.py', 'tasks.py', 'wire.py')),
                    ('bits_core/results', ('__init__.py', 'control.py', 'data.py', 'baseline.json'))):
                    for name in names:
                        copy(SOURCE / module / name, root / module / name)
                put(root / 'distribution/__init__.py', '')
                put(root / 'entry.py', 'import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).absolute().parent))\nfrom legacy_plugin.cli import entry\nraise SystemExit(entry())\n')
                put(stage / COMMAND.lstrip('/'), launch(PREFIX + '/entry.py'), True)
                if role == 'node':
                    extract_own(tmp / 'collector.tar.gz', root / 'components/collector')
                    put(root / 'components/collector/install-plugin-entry.py',
                        'import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).absolute().parent))\n'
                        'from bits_core.collector.install import main\nraise SystemExit(main())\n')
                    extract_own(SOURCE / 'finish-dist/mon-sensors-finish-0.3.0.tar.gz', root / 'components/finish')
                    for child in ('vendor', 'report-engine'):
                        shutil.copytree(str(native / child), str(root / child))
                    report = (SOURCE / 'distribution/report.py').read_text().replace('import common as native', 'from legacy_plugin import common as native')
                    put(root / 'report.py', report)
                    put(root / 'report-launcher', launch(PREFIX + '/report.py'), True)
                    launchers = []
                    for version in ('0.1.1', '0.2.0'):
                        for interpreter in ('/usr/libexec/platform-python3.6', '/usr/libexec/platform-python3.6m',
                                            '/usr/bin/python3.6', '/usr/local/bin/python3.6'):
                            raw = ('#!/bin/sh\n# Managed mon-sensors-report launcher v1.\nexec ' + interpreter +
                                   ' -I -S -B /opt/mon-sensors-report/' + version + '/report.py "$@"\n').encode()
                            launchers.append(hashlib.sha256(raw).hexdigest())
                    put(root / 'REPORT-LAUNCHERS.json', json.dumps(launchers))
                copy(SOURCE / 'docs/deployment/BITS.md', root / 'MANUAL.md')
                for path in stage.rglob('*'):
                    if path.is_symlink():
                        raise ValueError('Symlink in code payload')
                    path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
                files = {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*') if p.is_file()}
                put(root / 'PACKAGE.json', json.dumps({'role': role, 'version': VERSION, 'files': files,
                    'source_commit': os.environ['GITHUB_SHA'], 'workloads_source': os.environ['GITHUB_SHA'],
                    'component_versions': {'sckocp-api': component_builder.PUBLIC_API_VERSION,
                        'collector': component_builder.PLUGIN_VERSION, 'finish': '0.3.0', 'report': '0.2.0', 'workloads': '0.1.0-4'},
                    'services_started_by_install': False}, sort_keys=True, indent=2))
                if role == 'node' and kind == 'deb':
                    # Same hash-inventoried Python payload, for the first
                    # maintenance stop before native workload preinstall guards.
                    with tarfile.open(str(output / ('bits-o-node-' + VERSION + '-portable.tar.gz')), 'w:gz') as bundle:
                        def owned(info):
                            info.uid = info.gid = 0
                            info.uname = info.gname = 'root'
                            return info
                        bundle.add(str(root), arcname='bits-o-node-' + VERSION, filter=owned)
                package(stage, role, kind, output)
        packages = sorted(tools.rglob('bits-o-workloads*.rpm')) + sorted(tools.rglob('bits-o-workloads*.deb'))
        if len(packages) != 2:
            raise ValueError('Both renamed workload native packages are required')
        for file in packages:
            check_input(file)
            manifest = json.loads((file.parent / 'WORKLOADS.json').read_text())
            if manifest['source_commit'] != os.environ['GITHUB_SHA']:
                raise ValueError('Workloads must match this source commit')
            copy(file, output / file.name)
        api_archive = tmp / ('sckocp-api-' + component_builder.PUBLIC_API_VERSION + '.tar.gz')
        component_builder.public_api_package(api_archive)
        component_builder.self_extracting_package(api_archive,
            output / ('sckocp-api-' + component_builder.PUBLIC_API_VERSION + '.run'), 'install-sckocp-api.sh')
        # CI-only upgrade fixture, excluded from the published package set.
        copy(previous / 'standalone/mon-sensors-report-py36-0.2.0.run', output / 'ci/mon-sensors-report-py36-0.2.0.run')
        copy(SOURCE / 'docs/deployment/BITS.md', output / 'MANUAL.md')
        copy(SOURCE / 'docs/releases/0.2.4.md', output / 'OPTIMIZATION.md')
        put(output / 'SHA256SUMS', ''.join(sha(p) + '  ' + p.name + '\n' for p in sorted(output.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Build only in authorized cloud Linux')
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('legacy-dist'))
    args = parser.parse_args()
    build(args.baseline, args.output, args.tools)
