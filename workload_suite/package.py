"""Native package assembly in Linux. Payload has no service or install-time workload."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.0'
REVISION = '3'
PREFIX = '/opt/ocrun-workloads/' + VERSION


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def extract(inputs, work):
    lock = json.loads((ROOT / 'workload_suite/sources.json').read_text())
    for name, item in lock['sources'].items():
        if name == 'cpu2017':
            continue
        archive = inputs / (name + '.tar.gz')
        if sha(archive) != item['sha256']:
            raise ValueError('Invalid source checksum: ' + name)
        dest = work / name
        dest.mkdir(parents=True, exist_ok=False)
        with tarfile.open(str(archive)) as bundle:
            for member in bundle:
                p = PurePosixPath(member.name)
                if p.is_absolute() or '..' in p.parts:
                    raise ValueError('Unsafe archive path')
                parts = p.parts[item['strip']:]
                if not parts:
                    continue
                target = dest.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
                    os.utime(str(target), (member.mtime, member.mtime))
                elif name == 'mprime' and member.name.startswith('libgmp'):
                    continue  # Use the native OS's libgmp ABI; do not ship duplicate libraries.
                else:
                    raise ValueError('Unsupported archive entry: ' + member.name)


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(source), str(target))
    target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)


def stage_payload(work, stage):
    payload = stage / PREFIX.lstrip('/')
    payload.mkdir(parents=True)
    for name, source in [('stress', work / 'stress/src/stress'),
                         ('stress-ng', work / 'stress-ng/stress-ng'),
                         ('mbw', work / 'mbw/mbw'), ('cyclictest', work / 'cyclictest/cyclictest'),
                         ('mprime', work / 'mprime/mprime'), ('mlc', work / 'mlc/Linux/mlc')]:
        copy(source, payload / name / name)
    # Preserve Intel's bytes, documentation and notices; never strip/relink MLC.
    # Intel's archive lacks executable bits: normalize only the program's mode.
    (payload / 'mlc/mlc').chmod(0o755)
    copy(work / 'mlc/Documentation/readme_mlc_v3.13.rst', payload / 'mlc/readme_mlc_v3.13.rst')
    for name in ('Linux/redist.txt', 'Intel Memory Latency Tools Outbound License Agreement.pdf'):
        copy(work / 'mlc' / name, payload / 'licenses/mlc' / Path(name).name)
    ub = work / 'unixbench/UnixBench'
    for directory in ('pgms', 'testdir'):
        shutil.copytree(str(ub / directory), str(payload / 'unixbench' / directory))
    for name in ('Run', 'USAGE', 'README', 'Makefile'):
        copy(ub / name, payload / 'unixbench' / name)
    script = payload / 'unixbench/Run'
    data = script.read_text(encoding='utf-8')
    begin = data.index('    my $make = $ENV{MAKE} || "make";')
    end = data.index('    # Create a script to kill this run.', begin)
    # RPM/DEB contains prebuilt benchmarks. Do not compile or write under /opt at runtime.
    data = data[:begin] + ('    foreach my $prog (qw(dhry2reg whetstone-double execl fstime pipe context1 spawn looper syscall)) {\n'
                          '        -x "$BINDIR/$prog" or abortRun("Packaged benchmark missing: $prog");\n'
                          '    }\n\n') + data[end:]
    script.write_text(data, encoding='utf-8')
    script.chmod(0o755)
    for name in ('suite.py', 'cli.py', 'MANUAL.md', 'sources.json'):
        source = ROOT / ('docs/workloads/MANUAL.md' if name == 'MANUAL.md' else 'workload_suite/' + name)
        copy(source, payload / name)
    for path in (work / 'mprime').glob('*.txt'):
        copy(path, payload / 'licenses/mprime' / path.name)
    for name in ('stress', 'stress-ng', 'mbw', 'cyclictest', 'unixbench'):
        files = [p for p in (work / name).iterdir() if p.is_file() and
                 p.name.upper().startswith(('COPYING', 'LICENSE', 'COPYRIGHT'))]
        if not files:
            raise ValueError('Missing upstream license for ' + name)
        for p in files:
            copy(p, payload / 'licenses' / name / p.name)
    (stage / 'usr/bin').mkdir(parents=True)
    launcher = stage / 'usr/bin/ocrun-workloads'
    launcher.write_text('#!/bin/sh\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\nexport PATH\n'
                       'for p in /usr/bin/python3 /usr/libexec/platform-python; do\n'
                       '  if [ -x "$p" ]; then exec "$p" -I -S -B ' + PREFIX + '/cli.py "$@"; fi\n'
                       'done\necho "Python 3.6+ is required" >&2\nexit 69\n')
    launcher.chmod(0o755)
    files = {}
    for path in sorted(payload.rglob('*')):
        if path.is_symlink():
            raise ValueError('Payload must contain regular files, no links')
        if path.is_file():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
            files[path.relative_to(payload).as_posix()] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    spec = {'schema': 'ocrun-workloads-v1', 'version': VERSION, 'package_revision': REVISION, 'prefix': PREFIX,
            'source_commit': os.environ['OCRUN_SOURCE_COMMIT'], 'files': files,
            'sources': json.loads((payload / 'sources.json').read_text())['sources'],
            'compiled_isa': 'x86-64 generic; mprime selects native kernels at runtime',
            'external': ['SPEC CPU2017 1.0.5: original user-owned tree']}
    (payload / 'MANIFEST.json').write_text(json.dumps(spec, sort_keys=True, indent=2) + '\n')
    return payload


GUARD = '''#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
if ps -eo comm= | grep -Eq '^(ocb|oct|mon-sensors|mon-sensors-plu|stress|stress-ng.*|mprime|mlc|mbw|cyclictest|runcpu)$'; then
    echo 'Stop this test node scheduler/workloads before package changes; no process was stopped.' >&2
    exit 1
fi
'''

# Query the package manager's stored inventory, never execute an existing /opt
# launcher to decide whether it is safe to overwrite that launcher.
INSTALL_GUARD = '''
if [ -e /opt/ocrun-workloads/0.1.0 ] || [ -L /opt/ocrun-workloads/0.1.0 ] || [ -e /usr/bin/ocrun-workloads ] || [ -L /usr/bin/ocrun-workloads ]; then
    if command -v rpm >/dev/null; then
        rpm -q ocrun-workloads >/dev/null || { echo 'Unmanaged workload paths exist; preserved.' >&2; exit 1; }
        rpm -V ocrun-workloads || { echo 'Installed workload files changed; preserved.' >&2; exit 1; }
    else
        status=$(dpkg-query -W -f='${db:Status-Status}' ocrun-workloads 2>/dev/null) || exit 1
        [ "$status" = installed ] || { echo 'Unmanaged workload paths exist; preserved.' >&2; exit 1; }
        differences=$(dpkg --verify ocrun-workloads) || exit 1
        [ -z "$differences" ] || { printf '%s\\n' "$differences" >&2; echo 'Installed workload files changed; preserved.' >&2; exit 1; }
    fi
    [ ! -L /opt/ocrun-workloads/0.1.0 ] && [ ! -L /usr/bin/ocrun-workloads ] || exit 1
    unsafe=$(find /opt/ocrun-workloads/0.1.0 -xdev \\( ! -user root -o -perm /022 -o -type l -o -links +1 -type f \\) -print)
    [ -z "$unsafe" ] || { printf '%s\\n' "$unsafe" >&2; echo 'Unsafe workload ownership, permissions or links; preserved.' >&2; exit 1; }
fi
'''


def package(work, out):
    stage = work / 'package-root'
    stage.mkdir()
    payload = stage_payload(work, stage)
    shutil.copyfile(str(payload / 'MANIFEST.json'), str(out / 'WORKLOADS.json'))
    shutil.copyfile(str(payload / 'sources.json'), str(out / 'SOURCES.json'))
    shutil.copyfile(str(payload / 'MANUAL.md'), str(out / 'MANUAL.md'))
    if shutil.which('rpmbuild'):
        top = work / 'rpmbuild'
        (top / 'SPECS').mkdir(parents=True)
        spec = top / 'SPECS/ocrun-workloads.spec'
        spec.write_text('''Name: ocrun-workloads
Version: 0.1.0
Release: 3.el8
Summary: Version-pinned OCRUN x86-64 workload suite
License: GPLv2+ and GPLv3+ and GIMPS and LicenseRef-Intel-Limited-Tools
BuildArch: x86_64
Requires: python3 >= 3.6, perl, procps-ng, numactl-libs, gmp
AutoReqProv: yes
%global debug_package %{nil}
%global __os_install_post %{nil}
%description
Isolated OCRUN tools. No task is started by package installation.
%install
mkdir -p %{buildroot}
cp -a ''' + str(stage) + '''/. %{buildroot}/
%pre
''' + GUARD.split('\n', 1)[1] + INSTALL_GUARD + '''
%preun
''' + GUARD.split('\n', 1)[1] + '''
%files
%defattr(-,root,root,-)
/opt/ocrun-workloads/0.1.0
/usr/bin/ocrun-workloads
''')
        subprocess.run(['rpmbuild', '-bb', '--define', '_topdir ' + str(top), str(spec)], check=True)
        for p in top.rglob('*.rpm'):
            shutil.copyfile(str(p), str(out / p.name))
    else:
        control = stage / 'DEBIAN'
        control.mkdir()
        (control / 'control').write_text('''Package: ocrun-workloads
Version: 0.1.0-3
Architecture: amd64
Maintainer: OCRUN local deployment
Section: utils
Priority: optional
Depends: libc6 (>= 2.31), libnuma1, libgmp10, libatomic1, libstdc++6, python3 (>= 3.6), perl, procps
Description: Version-pinned OCRUN workload suite
 Private program paths, no network service and no automatic task start.
''')
        for name in ('preinst', 'prerm'):
            (control / name).write_text(GUARD + (INSTALL_GUARD if name == 'preinst' else ''))
            (control / name).chmod(0o755)
        # Debian's verification checks this package-manager-owned inventory.
        (control / 'md5sums').write_text(''.join(hashlib.md5(p.read_bytes()).hexdigest() + '  ' +
            p.relative_to(stage).as_posix() + '\n' for p in sorted(stage.rglob('*'))
            if p.is_file() and control not in p.parents))
        subprocess.run(['dpkg-deb', '-Zxz', '--uniform-compression', '--build', '--root-owner-group', str(stage),
                        str(out / 'ocrun-workloads_0.1.0-3_amd64.deb')], check=True)
    packages = list(out.glob('*.rpm')) + list(out.glob('*.deb'))
    (out / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.name + '\n' for p in packages))


if __name__ == '__main__':
    if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Build only in authorized GitHub Actions Linux')
    {'extract': extract, 'package': package}[sys.argv[1]](Path(sys.argv[2]), Path(sys.argv[3]))
