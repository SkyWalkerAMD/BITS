"""Package verified node upgrade materials only on the authorized Linux runner."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Cloud packaging only')
    target = ROOT / 'node-release/mon-sensors-finish-report-0.3.0'
    target.mkdir(parents=True)
    for package in ('finish-dist/mon-sensors-finish-0.3.0.run',):
        shutil.copyfile(str(ROOT / package), str(target / Path(package).name))
    for source, name in (('docs/node/OPERATIONS.md', 'OPTIMIZATION.md'), ('docs/node/AUTH.md', 'README-AUTH.md')):
        shutil.copyfile(str(ROOT / source), str(target / name))
    for name in ('finish-addon.json', 'report-addon.json', 'upgrade.json'):
        data = json.loads((ROOT / '.finish-results' / name).read_text())
        if data.get('status') != 'passed':
            raise ValueError('Cannot release a failed or missing cloud validation: ' + name)
        shutil.copyfile(str(ROOT / '.finish-results' / name), str(target / name))
    metadata = {'version': '0.3.0', 'component': 'mon-sensors-finish', 'report_version_unchanged': '0.2.0',
                'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip(),
                'run_url': 'https://github.com/{}/actions/runs/{}'.format(os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_RUN_ID']),
                'baseline': 'OCRUN 0.9.24a + plugin 0.12.9 + native sckocp 1.2.0 + API 0.3.1',
                'cloud_environment': 'Rocky Linux 8.10 / CPython 3.6.8, network none except loopback',
                'hardware_validation': '0.2.1 previously accepted; 0.3.0 new workload suite requires selected-node acceptance',
                'automatic_task_polling': False, 'management_server_changes': False}
    (target / 'RELEASE.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    (target / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
        for p in sorted(target.iterdir()) if p.is_file()))
    archive = target.parent / (target.name + '.tar.gz')
    with tarfile.open(str(archive), 'w:gz') as bundle:
        bundle.add(str(target), arcname=target.name)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (archive.parent / (archive.name + '.sha256')).write_text(checksum + '  ' + archive.name + '\n')
    print(json.dumps(dict(metadata, archive=archive.name, sha256=checksum)))


if __name__ == '__main__':
    main()
