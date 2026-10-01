"""Rebuild native packages for layout regression, never publish same-version assets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distribution.build import check_input, sha
from distribution.source_export import export_sources


def run(*args, **kwargs):
    subprocess.run(list(args), cwd=str(ROOT), check=True, **kwargs)


def main():
    if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Build only in authorized cloud Linux')
    output = ROOT / '.layout-results'
    output.mkdir(exist_ok=True)
    commit = os.environ['GITHUB_SHA']
    source = export_sources(output / 'customer-source.tar.gz', commit)
    run(sys.executable, 'build.py')
    run(sys.executable, 'tests/check_dist.py', '--runtime-only')
    run(sys.executable, '-m', 'bits_core.center.build')
    run(sys.executable, '-m', 'bits_core.results.build')
    run(sys.executable, 'bits_core/reporting/build_offline.py')
    # The legacy builder verifies the pinned release checksum and source commit,
    # preserves that provenance and exposes its original tool packages unchanged.
    run(sys.executable, '-m', 'legacy_plugin.build', '--baseline',
        '.layout-input/ocrun-system-0.2.2.tar.gz', '--tools', '.layout-input/workloads')
    legacy = ROOT / 'legacy-dist'
    with tempfile.TemporaryDirectory(prefix='layout-tools-') as temporary:
        unpacked = Path(temporary)
        tools_deb, = legacy.glob('bits-o-workloads*.deb')
        check_input(tools_deb)
        run('dpkg-deb', '-x', str(tools_deb), str(unpacked))
        manifest, = unpacked.glob('opt/ocrun-workloads/*/MANIFEST.json')
        metadata = json.loads(manifest.read_text())
        (legacy / 'WORKLOADS.json').write_bytes(manifest.read_bytes())
        env = dict(os.environ, OCRUN_SOURCE_COMMIT=commit,
                   OCRUN_COMPONENT_COMMIT=metadata['source_commit'])
        native = output / 'native'
        report, = (ROOT / 'report-dist').glob('*.tar.gz')
        for kind in ('rpm', 'deb'):
            tools, = legacy.glob('bits-o-workloads*.' + kind)
            run(sys.executable, '-m', 'distribution.build', '--role', 'node', '--kind', kind,
                '--tools', str(tools), '--report', str(report), '--output', str(native), env=env)
        nodes = sorted(native.glob('bits-node*'))
        server, = (ROOT / 'server-dist').glob('*.tar.gz')
        env['OCRUN_COMPONENT_COMMIT'] = commit  # server was built above from this revision
        for kind in ('rpm', 'deb'):
            run(sys.executable, '-m', 'distribution.build', '--role', 'center', '--kind', kind,
                '--server', str(server), '--nodes', *map(str, nodes), '--output', str(native), env=env)

    # Relocated documents keep the member names expected by offline consumers.
    required = [('server-dist', 'bits_core/center/MANUAL.md'),
                ('report-dist', 'README.md'), ('finish-dist', 'README.md'),
                ('control-dist', 'CONTROL-MANUAL.md')]
    for directory, suffix in required:
        archive, = (ROOT / directory).glob('*.tar.gz')
        with tarfile.open(str(archive)) as bundle:
            if not any(n == suffix or n.endswith('/' + suffix) for n in bundle.getnames()):
                raise ValueError('Missing compatible manual in ' + str(archive))
    packages = {}
    for directory in (output / 'native', legacy):
        for path in sorted(directory.iterdir()):
            if path.suffix not in ('.rpm', '.deb') or path.name.startswith('bits-o-workloads'):
                continue
            check_input(path)
            packages[path.name] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    if len(packages) != 8:
        raise ValueError('Expected complete-system and legacy-plugin packages in both formats')
    result = {'status': 'built', 'source_commit': commit, 'packages': packages,
              'customer_sources': source, 'tool_source_commit': metadata['source_commit'],
              'scope': 'layout regression build; verification artifacts only; no release replacement'}
    (output / 'packages.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
