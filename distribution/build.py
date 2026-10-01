"""Cloud-only native system packages assembled with the built workload payload."""
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

SOURCE = Path(__file__).resolve().parents[1]
VERSION = '0.3.0'
NODE = '/opt/bits/node/' + VERSION
CENTER = '/opt/bits/center/' + VERSION
APP = '/var/lib/bits/node/app'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def put(path, data, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
    path.chmod(0o755 if executable else 0o644)


def copy(source, target):
    put(target, source.read_bytes(), bool(source.stat().st_mode & 0o111))


def check_input(path):
    entries = (path.parent / 'SHA256SUMS').read_text().splitlines()
    inventory = {line.split()[1]: line.split()[0] for line in entries}
    if inventory.get(path.name) != sha(path):
        raise ValueError('Input artifact hash differs: ' + str(path))


def launch(target, arguments=''):
    return ('#!/bin/sh\nset -eu\nPATH=/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin\nexport PATH\n'
            'unset PYTHONPATH PYTHONHOME PYTHONSTARTUP\n'
            'for p in /usr/bin/python3 /usr/libexec/platform-python; do\n'
            ' if [ -x "$p" ]; then exec "$p" -I -S -B ' + target + ' ' + arguments + ' "$@"; fi\n'
            'done\necho "Python 3.6+ required" >&2\nexit 69\n')


def template(root):
    app = root / 'template'
    sys.path.insert(0, str(SOURCE))
    from bits_core.collector.install import FILES
    from bits_core.layout import NATIVE as layout
    from bits_core.packaging import write_layout
    helper = app / layout.collector_dir
    for name in FILES:
        target = layout.collector_program if name == 'mon-sensors-plugin' else name
        put(helper / target, (SOURCE / name).read_bytes().replace(b'\r\n', b'\n'), name == 'mon-sensors-plugin')
    write_layout(helper, native=True)
    collector_files = {p.relative_to(helper).as_posix(): sha(p) for p in helper.rglob('*') if p.is_file()}
    plugin = launch(APP + '/' + layout.collector_dir + '/' + layout.collector_program)
    put(app / layout.collector_entry, plugin, True)
    put(helper / layout.collector_marker, json.dumps({'owner': 'mon-sensors-plugin-v1',
        'launcher_sha256': hashlib.sha256(plugin.encode()).hexdigest(),
        'layout': layout.name, 'files': collector_files}))
    put(app / layout.backend, 'sckocp\n')
    finalizer = app / layout.helper
    names = ('common.py', 'finish.py', 'node.py', 'queue.py', 'workload.py',
             'operator_cli.py', 'install_guard.py', 'tools_adoption.py', 'report_sheet.py')
    for name in names:
        put(finalizer / name, (SOURCE / 'bits_core/batch' / name).read_bytes().replace(b'\r\n', b'\n'))
    put(finalizer / 'security.py', (SOURCE / 'sckocp_api/security.py').read_bytes().replace(b'\r\n', b'\n'))
    put(finalizer / 'suite.py', (SOURCE / 'bits_core/workloads/suite.py').read_bytes().replace(b'\r\n', b'\n'))
    write_layout(finalizer, native=True)
    put(app / layout.scheduler, '#!/bin/sh\nset -eu\n[ "${1:-run}" = run ] || exit 2\nexec /usr/bin/bits-node run\n', True)
    put(app / layout.entry, launch(NODE + '/finish_entry.py',
        APP + '/' + layout.helper + '/finish.py --app ' + APP), True)
    managed = {p.relative_to(app).as_posix(): sha(p) for p in app.rglob('*') if p.is_file()}
    put(app / layout.install_marker, json.dumps({'version': '0.3.0', 'layout': layout.name,
        'app': APP, 'distribution': VERSION, 'managed_files': managed, 'detached': False}))


def extract_own(archive, target):
    with tarfile.open(str(archive)) as bundle:
        for item in bundle:
            name = Path(item.name)
            if name.is_absolute() or '..' in name.parts or not (item.isfile() or item.isdir()):
                raise ValueError('Unexpected internal payload entry: ' + item.name)
        bundle.extractall(str(target))


def node_stage(stage, tools, report):
    # Both original native packages are our own checksum-verified cloud outputs.
    check_input(tools)
    check_input(report)
    if json.loads((tools.parent / 'WORKLOADS.json').read_text())['source_commit'] != os.environ.get('OCRUN_COMPONENT_COMMIT', os.environ['OCRUN_SOURCE_COMMIT']):
        raise ValueError('Tool package was built from a different source revision')
    if tools.suffix == '.deb':
        subprocess.run(['dpkg-deb', '-x', str(tools), str(stage)], check=True)
    else:
        cp = subprocess.Popen(['rpm2cpio', str(tools)], stdout=subprocess.PIPE)
        try:
            # RPM generates /usr/lib/.build-id links separately. Select only our
            # owned application paths; never import/debug-resolve those links.
            subprocess.run(['cpio', '-idm', '--quiet', '--no-absolute-filenames',
                            './opt/ocrun-workloads/*', './usr/bin/bits-o-workloads'],
                           cwd=str(stage), stdin=cp.stdout, check=True)
        finally:
            cp.stdout.close()
        if cp.wait():
            raise ValueError('Cannot unpack built workload RPM')
    # Relocate only our verified package payload. Third-party program bytes
    # remain unchanged; the native launcher/profile and manifest are rebuilt.
    from bits_core.packaging import write_layout
    old_tools = stage / 'opt/ocrun-workloads/0.1.0'
    native_tools = stage / 'opt/bits/workloads/0.1.0'
    native_tools.parent.mkdir(parents=True, exist_ok=True)
    old_tools.rename(native_tools)
    (stage / 'usr/bin/bits-o-workloads').unlink()
    write_layout(native_tools, native=True)
    workload_manifest = json.loads((native_tools / 'MANIFEST.json').read_text())
    workload_manifest['layout'] = 'bits-v1'
    workload_manifest['files']['bits_layout.py'] = {'sha256': sha(native_tools / 'bits_layout.py'),
                                                  'bytes': (native_tools / 'bits_layout.py').stat().st_size}
    put(native_tools / 'MANIFEST.json', json.dumps(workload_manifest, sort_keys=True, indent=2))
    put(stage / 'usr/libexec/bits-workloads', launch('/opt/bits/workloads/0.1.0/cli.py'), True)
    root = stage / NODE.lstrip('/')
    for name in ('node.py', 'report.py', 'finish_entry.py', 'common.py'):
        copy(SOURCE / 'distribution' / name, root / name)
    for name in ('common.py', 'streaming.py'):
        copy(SOURCE / 'bits_core/reporting' / name, root / 'report-engine' / name)
    # Reuse only the pure-Python XLSX writer, never old CPython/NumPy ABI wheels.
    with tempfile.TemporaryDirectory(prefix='ocrun-report-input-') as temporary:
        extract_own(report, Path(temporary))
        vendor = Path(temporary) / 'payload/vendor'
        for child in vendor.iterdir():
            if child.name == 'xlsxwriter' or child.name.lower().startswith('xlsxwriter-'):
                shutil.copytree(str(child), str(root / 'vendor' / child.name))
        provenance = json.loads((Path(temporary) / 'payload/PROVENANCE.json').read_text())
        put(root / 'REPORT-SOURCE.json', json.dumps([v for v in provenance if v['project'] == 'XlsxWriter']))
    for name in ('__init__.py', 'connection.py', 'safe.py', 'tasks.py', 'wire.py'):
        copy(SOURCE / 'bits_core/center' / name, root / 'center-code/server_deploy' / name)
    template(root)
    write_layout(root, native=True)
    put(stage / 'usr/libexec/bits-report', launch(NODE + '/report.py'), True)
    put(stage / 'usr/bin/bits-node', launch(NODE + '/node.py'), True)
    return root


def center_stage(stage, node_packages, server):
    check_input(server)
    root = stage / CENTER.lstrip('/')
    for name in ('center.py', 'common.py', 'network.py'):
        copy(SOURCE / 'distribution' / name, root / name)
    for name in ('__init__.py', 'control.py', 'data.py', 'baseline.json'):
        copy(SOURCE / 'bits_core/results' / name, root / 'bits_core/results' / name)
    with tempfile.TemporaryDirectory(prefix='bits-center-input-') as temporary:
        extract_own(server, Path(temporary))
        candidates = list(Path(temporary).iterdir())
        if len(candidates) != 1:
            raise ValueError('Unexpected center archive root')
        if json.loads((candidates[0] / 'PACKAGE.json').read_text())['source_commit'] != os.environ.get('OCRUN_COMPONENT_COMMIT', os.environ['OCRUN_SOURCE_COMMIT']):
            raise ValueError('Center source revision differs')
        shutil.copytree(str(candidates[0]), str(root / 'server'))
    files = {}
    for package in node_packages:
        check_input(package)
        copy(package, root / 'releases' / package.name)
        files[package.name] = sha(package)
    put(root / 'NODE-PACKAGES.json', json.dumps(files, sort_keys=True, indent=2))
    put(stage / 'usr/bin/bits-center', launch(CENTER + '/center.py'), True)
    return root


def guards(role, kind, prefix, command):
    package = 'bits-' + role
    ownership = '''
if [ -e PREFIX ] || [ -L PREFIX ] || [ -e COMMAND ] || [ -L COMMAND ]; then
 if [ -e PREFIX ] || [ -L PREFIX ]; then
  if [ 'KIND' = rpm ]; then
   owner=$(rpm -qf --qf '%%{NAME}' PREFIX/MANIFEST_FILE) || exit 1
   [ "$owner" = PACKAGE ] || exit 1
  else
   owner=$(dpkg-query -S PREFIX/MANIFEST_FILE) || exit 1
   [ "$owner" = 'PACKAGE: PREFIX/MANIFEST_FILE' ] || exit 1
  fi
 fi
 if [ 'KIND' = rpm ]; then
  rpm -q PACKAGE >/dev/null && rpm -V PACKAGE || { echo 'Unmanaged/modified package paths retained.' >&2; exit 1; }
 else
  package_state=$(dpkg-query -W -f='${db:Status-Status}' PACKAGE) || exit 1
  case "$package_state" in
   installed|unpacked|half-configured|half-installed) ;;
   *) echo "Package inventory unavailable ($package_state); existing files retained." >&2; exit 1 ;;
  esac
  inventory=$(dpkg-query --control-path PACKAGE md5sums) || exit 1
  [ -s "$inventory" ] || { echo 'Package checksums missing; existing files retained.' >&2; exit 1; }
  differences=$(dpkg --verify PACKAGE)
  [ -z "$differences" ] || { printf '%s\\n' "$differences" >&2; exit 1; }
 fi
 for owned in PREFIX PREVIOUS_PREFIX COMMAND; do
  if [ -e "$owned" ] || [ -L "$owned" ]; then
   [ ! -L "$owned" ] || exit 1
   unsafe=$(find "$owned" -xdev \\( ! -user root -o -perm /7022 -o -type l -o -links +1 -type f \\) -print)
   [ -z "$unsafe" ] || { echo 'Unsafe package files retained.' >&2; exit 1; }
  fi
 done
fi
'''.replace('PREVIOUS_PREFIX', '/opt/ocrun-' + role + '/0.2.2').replace('PREFIX', prefix).replace('COMMAND', command).replace('PACKAGE', package).replace('KIND', kind).replace('MANIFEST_FILE', 'PACKAGE.json')
    active = ('[ ! -e /etc/bits/node/native.json ] || { echo "Detach this native node before package replacement/removal; results are retained." >&2; exit 1; }\n'
              if role == 'node' else
              '[ ! -e /etc/bits/center/manifest.json ] || { echo "Roll back this center deployment before package replacement/removal; data is retained." >&2; exit 1; }\n')
    base = ('#!/bin/sh\nset -eu\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\nexport PATH\n'
            'for tool in ps grep find; do\n'
            ' command -v "$tool" >/dev/null || { echo "Required safety tool missing: $tool" >&2; exit 1; }\n'
            'done\n') + active
    if role == 'node':
        from bits_core.workloads.package import GUARD
        base += GUARD.split('export PATH\n', 1)[1]
        ownership += '''
if [ -e /opt/bits/workloads/0.1.0 ] || [ -L /opt/bits/workloads/0.1.0 ] || [ -e /usr/libexec/bits-workloads ] || [ -L /usr/libexec/bits-workloads ]; then
 if [ 'KIND' = rpm ]; then
  rpm -q bits-node >/dev/null && rpm -V bits-node || exit 1
 else
  package_state=$(dpkg-query -W -f='${db:Status-Status}' bits-node) || exit 1
  case "$package_state" in installed|unpacked|half-configured|half-installed) ;; *) exit 1 ;; esac
  inventory=$(dpkg-query --control-path bits-node md5sums) || exit 1
  [ -s "$inventory" ] || exit 1
  [ -z "$(dpkg --verify bits-node)" ] || exit 1
 fi
 [ ! -L /opt/bits/workloads/0.1.0 ] && [ ! -L /usr/libexec/bits-workloads ] || exit 1
 unsafe=$(find /opt/bits/workloads/0.1.0 /usr/libexec/bits-workloads -xdev \\( ! -user root -o -perm /7022 -o -type l -o -links +1 -type f \\) -print)
 [ -z "$unsafe" ] || exit 1
fi
'''.replace('KIND', kind)
    # During a real removal preserve local modifications too. During RPM upgrade
    # the old preun runs after replacement; the new pre-install already checked it.
    removal = base + '\ncase "${1:-}" in\n0|remove|deconfigure)\n' + ownership + '\n;;\nesac\n'
    return base + ownership, removal


def package(role, kind, stage, root, out):
    from build import PUBLIC_API_VERSION, PLUGIN_VERSION
    files = {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*') if p.is_file()}
    put(root / 'PACKAGE.json', json.dumps({'role': role, 'version': VERSION, 'files': files,
        'component_versions': {'sckocp-api': PUBLIC_API_VERSION, 'mon-sensors-plugin': PLUGIN_VERSION},
        'source_commit': os.environ['OCRUN_SOURCE_COMMIT'], 'services_started_by_install': False}, sort_keys=True, indent=2))
    prefix = NODE if role == 'node' else CENTER
    command = '/usr/bin/bits-' + role
    pre, remove = guards(role, kind, prefix, command)
    name = 'bits-' + role
    if kind == 'rpm':
        top = stage.parent / ('rpmbuild-' + role)
        for directory in ('BUILD', 'RPMS', 'SOURCES', 'SPECS', 'SRPMS', 'BUILDROOT'):
            (top / directory).mkdir(parents=True, exist_ok=True)
        spec = top / 'SPECS' / (name + '.spec')
        requirements = 'bash, python3 >= 3.6, procps-ng, util-linux, findutils, curl, rsync'
        if role == 'center':
            requirements += ', iproute'
        extra = 'Conflicts: ocrun-node, ocrun-center, ocrun-plugin-node, ocrun-plugin-control, bits-o-node, bits-o-control\n'
        listed = prefix + '\n' + command + '\n'
        if role == 'node':
            requirements += ', perl, numactl-libs, gmp, (redis or valkey)'
            extra += 'Conflicts: ocrun-workloads, bits-o-workloads\n'
            listed += '/opt/bits/workloads/0.1.0\n/usr/libexec/bits-workloads\n/usr/libexec/bits-report\n'
        spec.write_text('Name: ' + name + '\nVersion: ' + VERSION + '\nRelease: 1.el8\n'
            'Summary: BITS integrated ' + role + '\nLicense: GPLv2+ and GPLv3+ and GIMPS and LicenseRef-Intel-Limited-Tools\nBuildArch: x86_64\n'
            'Requires: ' + requirements + '\nRequires(pre): procps-ng, findutils, grep\n' + extra +
            '%global debug_package %{nil}\n%global __os_install_post %{nil}\n%description\n'
            'Explicit BITS setup. Package installation never starts tasks or services.\n'
            '%install\nmkdir -p %{buildroot}\ncp -a ' + str(stage) + '/. %{buildroot}/\n'
            '%pre\n' + pre.split('\n', 1)[1] + '\n%preun\n' + remove.split('\n', 1)[1] +
            '\n%files\n%defattr(-,root,root,-)\n' + listed)
        subprocess.run(['rpmbuild', '-bb', '--define', '_topdir ' + str(top), str(spec)], check=True)
        built = next(top.rglob('*.rpm'))
        copy(built, out / built.name)
    else:
        control = stage / 'DEBIAN'
        control.mkdir()
        requirements = 'bash, python3 (>= 3.6), procps, util-linux, findutils, curl, rsync'
        if role == 'center':
            requirements += ', iproute2'
        conflicts = 'ocrun-node, ocrun-center, ocrun-plugin-node, ocrun-plugin-control, bits-o-node, bits-o-control'
        if role == 'node':
            requirements += ', libc6 (>= 2.31), libnuma1, libgmp10, libatomic1, libstdc++6, perl, redis-tools'
            conflicts += ', ocrun-workloads, bits-o-workloads'
        extra = 'Conflicts: ' + conflicts + '\n'
        put(control / 'control', 'Package: ' + name + '\nVersion: ' + VERSION + '-1\nArchitecture: amd64\n'
            'Maintainer: OCRUN local deployment\nSection: admin\nPriority: optional\nPre-Depends: procps, findutils, grep\nDepends: ' + requirements + '\n' + extra +
            'Description: Integrated BITS ' + role + '\n Explicit setup, no installation-time task or service startup.\n')
        put(control / 'preinst', pre, True)
        put(control / 'prerm', remove, True)
        put(control / 'md5sums', ''.join(hashlib.md5(p.read_bytes()).hexdigest() + '  ' + p.relative_to(stage).as_posix() + '\n'
            for p in sorted(stage.rglob('*')) if p.is_file() and control not in p.parents))
        # Debian 11 cannot read zstd members, the Ubuntu builder's default.
        # Use xz for both control and data archives across all supported dpkg versions.
        subprocess.run(['dpkg-deb', '-Zxz', '--uniform-compression', '--build', '--root-owner-group', str(stage),
                        str(out / (name + '_' + VERSION + '-1_amd64.deb'))], check=True)


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Build only in the authorized cloud Linux environment')
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('node', 'center'), required=True)
    parser.add_argument('--kind', choices=('rpm', 'deb'), required=True)
    parser.add_argument('--tools', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--server', type=Path)
    parser.add_argument('--nodes', type=Path, nargs='*')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(SOURCE))
    with tempfile.TemporaryDirectory(prefix='ocrun-native-') as temporary:
        stage = Path(temporary) / 'root'
        stage.mkdir()
        root = (node_stage(stage, args.tools, args.report) if args.role == 'node'
                else center_stage(stage, args.nodes, args.server))
        for path in stage.rglob('*'):
            if path.is_symlink():
                raise ValueError('Native package payload must not contain links')
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
        from build import document_bytes
        put(root / 'MANUAL.md', document_bytes('docs/deployment/BITS.md', 'MANUAL.md',
                                              {'docs/deployment/BITS.md': 'MANUAL.md'}))
        package(args.role, args.kind, stage, root, args.output)
    packages = sorted(list(args.output.glob('*.rpm')) + list(args.output.glob('*.deb')))
    put(args.output / 'SHA256SUMS', ''.join(sha(p) + '  ' + p.name + '\n' for p in packages))


if __name__ == '__main__':
    main()
