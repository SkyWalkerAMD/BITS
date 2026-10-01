"""Download pinned inputs in cloud; never discover mutable 'latest' at build time."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys


def main():
    if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Source preparation/build only in GitHub Actions Linux')
    root = Path(__file__).resolve().parent
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    spec = json.loads((root / 'sources.json').read_text())
    for name, item in spec['sources'].items():
        if name == 'cpu2017':
            continue
        path = out / (name + '.tar.gz')
        cached = root / 'vendor' / path.name
        if cached.exists():
            shutil.copyfile(str(cached), str(path))
        else:
            subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '20',
                            '--max-time', '300', '--proto', '=https', '--proto-redir', '=https',
                            '--output', str(path), item['url']], check=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Upstream archive checksum changed: ' + name)
        print(name + ' ' + item['version'] + ' verified', flush=True)
    (out / 'sources.json').write_text(json.dumps(spec, indent=2) + '\n')


if __name__ == '__main__':
    main()
