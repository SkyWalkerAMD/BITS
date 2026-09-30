"""CI-only repair for missing Debian security pool files, without changing APT trust.

APT verifies the current signed indexes. Missing *identical* package bytes are
retrieved from Debian's official snapshot service, checked against SHA-256 in
those indexes, then given back to APT's cache. No downgrade, expired index,
third-party mirror, signature override, or production repository edit.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request


def run(args):
    try:
        return subprocess.check_output(args, stderr=subprocess.STDOUT).decode('utf-8')
    except subprocess.CalledProcessError as error:
        print(error.output.decode('utf-8', 'replace'), file=sys.stderr, flush=True)
        raise


def download(url):
    for n in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except OSError:
            if n == 3:
                raise
            time.sleep(n + 1)


def prepare(mode, tag, local_package=None):
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Only disposable cloud CI containers')
    container = 'ocrun-debian11-deps-' + str(os.getpid())
    out = Path('.workload-results' if mode == 'runtime' else 'workload-dist')
    out.mkdir(exist_ok=True)
    cache = out / 'debian11-cache'
    cache.mkdir(exist_ok=True)
    run(['docker', 'create', '--name', container, 'debian:11', 'sleep', 'infinity'])
    run(['docker', 'start', container])
    def execute(*args):
        return run(['docker', 'exec', container] + list(args))
    try:
        print(execute('apt-get', 'update'), flush=True)
        packages = ['python3', 'tar', 'gzip', 'ca-certificates', 'perl', 'make', 'gcc', 'libc6-dev',
                    'libgmp10', 'libnuma1', 'libatomic1', 'procps', 'util-linux']
        if mode == 'build':
            packages += ['build-essential', 'autoconf', 'automake', 'dpkg-dev', 'libnuma-dev']
        elif mode in ('server', 'native'):
            packages = ['systemd', 'systemd-sysv', 'dbus', 'procps', 'curl', 'iproute2', 'python3',
                        'nginx', 'rsync', 'ca-certificates', 'tar', 'gzip', 'util-linux', 'nftables',
                        'redis-server', 'redis-tools']
            if mode == 'native':
                packages += ['perl', 'libnuma1', 'libgmp10', 'libatomic1', 'libstdc++6']
        elif mode == 'legacy':
            packages = ['python3', 'rsync', 'redis-server', 'redis-tools', 'curl', 'tar', 'gzip',
                        'ca-certificates', 'util-linux', 'procps', 'findutils', 'perl', 'libnuma1',
                        'libgmp10', 'libatomic1', 'libstdc++6', 'iputils-ping', 'dmidecode', 'passwd']
        elif mode == 'security':
            packages = ['python3', 'python3-venv', 'tar', 'gzip', 'ca-certificates']
        else:
            run(['docker', 'cp', str(local_package), container + ':/tmp/bits-o-workloads.deb'])
            packages = ['/tmp/bits-o-workloads.deb']
        plan = execute('apt-get', '-y', '--no-install-recommends', '--download-only', '--print-uris', 'install', *packages)
        requests = []
        for line in plan.splitlines():
            if not line.startswith("'http"):
                continue
            # Some APT versions omit the optional checksum from --print-uris.
            # Trust SHA-256 from the verified package index below in either case.
            parts = shlex.split(line)
            if len(parts) not in (3, 4):
                raise ValueError('Unexpected APT download plan: ' + line)
            url, filename, size = parts[:3]
            package = filename.split('_', 1)[0]
            blocks = execute('apt-cache', 'show', package).split('\n\n')
            selected = None
            for block in blocks:
                fields = dict(row.split(': ', 1) for row in block.splitlines() if ': ' in row and not row.startswith(' '))
                expected = urllib.parse.unquote(url.rsplit('/', 1)[-1])
                if fields.get('Filename', '').rsplit('/', 1)[-1] == expected:
                    selected = fields
                    break
            if not selected or 'SHA256' not in selected:
                raise ValueError('No SHA-256 from signed APT metadata for ' + filename)
            requests.append((url, filename, selected))

        def fetch(item):
            url, filename, meta = item
            source = url.replace('http://', 'https://', 1)
            try:
                data = download(source)
            except OSError:
                api = 'https://snapshot.debian.org/mr/binary/{}/{}/binfiles'.format(
                    urllib.parse.quote(meta['Package'], safe=''), urllib.parse.quote(meta['Version'], safe=''))
                records = json.loads(download(api))['result']
                candidates = [r for r in records if r['architecture'] == meta['Architecture']]
                data = None
                for candidate in candidates:
                    source = 'https://snapshot.debian.org/file/' + candidate['hash']
                    content = download(source)
                    if hashlib.sha256(content).hexdigest() == meta['SHA256']:
                        data = content
                        break
                if data is None:
                    raise ValueError('No matching official snapshot bytes: ' + filename)
            if hashlib.sha256(data).hexdigest() != meta['SHA256']:
                raise ValueError('APT index SHA-256 mismatch: ' + filename)
            (cache / filename).write_bytes(data)
            print('verified ' + filename, flush=True)
            return {'package': meta['Package'], 'version': meta['Version'], 'sha256': meta['SHA256'], 'source': source}

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(fetch, requests))
        (out / 'debian11-dependency-receipt.json').write_text(json.dumps(receipts, indent=2))
        execute('mkdir', '-p', '/var/cache/apt/archives/partial')
        for p in cache.glob('*.deb'):
            run(['docker', 'cp', str(p), container + ':/var/cache/apt/archives/' + p.name])
        install_packages = (['{}={}'.format(r['package'], r['version']) for r in receipts]
                            if mode == 'runtime' else packages)
        print(execute('env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-y', '--no-install-recommends',
                      '--no-download', 'install', *install_packages), flush=True)
        if mode == 'runtime':
            # Bullseye APT --no-download has a local-file path resolution bug.
            # Resolve/install its exact signed-index dependencies first, then
            # let dpkg install the already-built local package with no network.
            print(execute('dpkg', '-i', '/tmp/bits-o-workloads.deb'), flush=True)
        if mode in ('server', 'native'):
            execute('mkdir', '-p', '/fixture')
            for source, name in [('server_deploy/dependencies.sh', 'dependencies.sh'),
                                 ('ci/server_image.sh', 'server_image.sh')]:
                run(['docker', 'cp', source, container + ':/fixture/' + name])
            print(execute('env', 'GITHUB_ACTIONS=true', 'bash', '/fixture/server_image.sh'), flush=True)
            # These packages were installed into this new disposable fixture by
            # the verified-cache step, so dependencies.sh correctly sees them
            # as pre-existing. Disable only their fresh default autostart links
            # before first boot, just as the normal fresh-image path does.
            print(execute('systemctl', 'disable', 'nginx.service', 'redis-server.service', 'rsync.service'), flush=True)
        if mode == 'native':
            execute('mkdir', '-p', '/root/native-packages')
            for package in sorted(local_package.rglob('*.deb')):
                run(['docker', 'cp', str(package), container + ':/root/native-packages/' + package.name])
                print(execute('dpkg', '-i', '/root/native-packages/' + package.name), flush=True)
        if mode == 'legacy':
            execute('mkdir', '-p', '/root/plugin-packages')
            run(['docker', 'cp', str(local_package) + '/.', container + ':/root/plugin-packages/'])
            for pattern in ('bits-o-workloads*.deb', 'bits-o-control*.deb'):
                selected = list(local_package.glob(pattern))
                if len(selected) != 1:
                    raise ValueError('Ambiguous legacy fixture package')
                print(execute('dpkg', '-i', '/root/plugin-packages/' + selected[0].name), flush=True)
        run(['docker', 'commit', container, tag])
    finally:
        run(['docker', 'rm', '-f', container])


if __name__ == '__main__':
    prepare(sys.argv[1], sys.argv[2], Path(sys.argv[3]) if len(sys.argv) > 3 else None)
