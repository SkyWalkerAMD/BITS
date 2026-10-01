import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from build import DOC_PATHS


def check_content(bundle, name):
    expected = (root / DOC_PATHS.get(name, name)).read_bytes().replace(b'\r\n', b'\n')
    actual = bundle.extractfile(name).read()
    if name.endswith('.md'):
        # The prose and command examples must survive relocation unchanged.
        # Destinations may be rebased, but local links must resolve in the package.
        links = rb'(\[[^\]\n]*\]\()([^\s)]+)\)'
        assert re.sub(links, rb'\1)', actual) == re.sub(links, rb'\1)', expected), name
        import posixpath
        from urllib.parse import unquote, urlsplit
        for unused, raw in re.findall(links, actual):
            url = urlsplit(raw.decode('utf-8'))
            if url.scheme or url.netloc or not url.path:
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.path)))
            assert target in bundle.getnames(), 'Broken packaged manual link: ' + name + ' -> ' + target
    else:
        assert actual == expected, 'Stale runtime ' + name

parser = argparse.ArgumentParser()
parser.add_argument('--runtime-only', action='store_true', help='Verify a source-only cloud build')
args = parser.parse_args()
manifest = json.loads((root / 'dist/manifest.json').read_text(encoding='utf8'))
for kind in (('runtime',) if args.runtime_only else ('runtime', 'tools')):
    item = manifest[kind]
    path = root / 'dist' / item['file']
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    assert digest.hexdigest() == item['sha256'], kind + ' checksum mismatch'
    with tarfile.open(path, 'r:gz') as bundle:
        members = bundle.getmembers()
        paths = {member.name: member for member in members}
        assert len(paths) == len(members), 'Duplicate archive members'
        assert not any(name.startswith('/') or '..' in Path(name).parts for name in paths)
        if kind == 'runtime':
            for name in ('install-sckocp-api.sh', 'installer-python.sh', 'sckocp_api/install.py'):
                assert name in paths, 'Missing automated installer file ' + name
            for name in ('ocrun/agent.py', 'ocrun/cpu_burn.py', 'ocrun/admin.py', 'ocrun/sckocp.py', 'ocrun/sckocp_metrics.py', 'ocrun/mon_sensors.py', 'bits_core/collector/__init__.py', 'bits_core/collector/collector.py', 'bits_core/collector/install.py', 'sckocp_api/provider.py', 'sckocp_api/__init__.py', 'sckocp_api/interface.py', 'sckocp_api/cli.py', 'sckocp_api/__main__.py', 'sckocp-api', 'mon-sensors-plugin', 'install-mon-sensors-plugin.sh', 'install-server.sh', 'install-client.sh', 'README.md', 'SCKOCP.md', 'SCKOCP-API.md', 'MON-SENSORS.md', 'PACKAGES.md'):
                assert name in paths, 'Missing runtime file ' + name
                check_content(bundle, name)
            assert 'sckocp_api/security.py' in paths
            assert not any('.testdeps' in name or 'server.json' in name or 'agent.json' in name for name in paths)
        else:
            for name in ('bin/p95-no_m1/mprime', 'bin/mlc/mlc', 'bin/mbw/mbw', 'bin/bc/bcr'):
                assert name in paths, 'Missing workload ' + name
                assert paths[name].mode & 0o111, 'Not executable: ' + name
            assert not any(member.issym() and member.linkname.startswith('/') for member in members)
    print(kind + ': checksum, members, and package content PASS')

# These archives must remain usable independently, without bundling OCRUN's
# control plane into the device plugin or hardware consumer SDK.
for kind, entry in (('mon_sensors_plugin', 'mon-sensors-plugin'), ('public_api', 'sckocp-api')):
    item = manifest[kind]
    path = root / 'dist' / item['file']
    assert path.stat().st_size == item['bytes']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
    with tarfile.open(path, 'r:gz') as bundle:
        members = bundle.getmembers()
        names = [member.name for member in members]
        assert len(names) == len(set(names))
        assert entry in names and bundle.getmember(entry).mode & 0o111
        assert 'sckocp_api/security.py' in names
        assert 'mon-sensors' not in names and 'install-server.sh' not in names
        assert not any(name.startswith(('ocrun/', 'drafts/', 'integrations/', 'research/', 'ci/')) for name in names)
        assert all(member.isfile() and not member.name.startswith('/') and
                   '..' not in Path(member.name).parts for member in members)
        for name in names:
            check_content(bundle, name)
        if kind == 'public_api':
            assert not any(name.startswith('bits_core/collector/') for name in names)
            assert {'install-sckocp-api.sh', 'installer-python.sh', 'sckocp_api/install.py',
                    'sckocp_api/bootstrap.py'}.issubset(names)
            expected_modules = {'sckocp_api/' + name for name in (
                '__init__.py', '__main__.py', 'cli.py', 'interface.py', 'provider.py',
                'security.py', 'install.py', 'bootstrap.py')}
            assert set(names) == expected_modules | {
                'installer-python.sh', 'sckocp-api', 'install-sckocp-api.sh',
                'SCKOCP-API.md', 'docs/API-SECURITY.md', 'examples/read-sckocp.py'}
        else:
            assert {'bits_core/collector/collector.py', 'bits_core/collector/install.py',
                    'bits_core/collector/runtime.py', 'bits_core/collector/adoption.py',
                    'install-mon-sensors-plugin.sh', 'installer-python.sh', 'sckocp_api/interface.py'}.issubset(names)
    print(kind + ': independent archive, entrypoint, content and checksum PASS')

for kind, archive_kind in (('public_api_installer', 'public_api'),
                           ('mon_sensors_installer', 'bits_core/collector')):
    item = manifest[kind]
    installer = (root / 'dist' / item['file']).read_bytes()
    assert len(installer) == item['bytes']
    assert hashlib.sha256(installer).hexdigest() == item['sha256']
    header, payload = installer.split(b'# __SCKOCP_ARCHIVE_BELOW__\n', 1)
    assert payload == (root / 'dist' / manifest[archive_kind]['file']).read_bytes()
    assert hashlib.sha256(payload).hexdigest().encode('ascii') in header
    assert b'@OFFSET@' not in header and b'@INSTALLER@' not in header
    print(kind + ': embedded archive and distribution checksums PASS')
