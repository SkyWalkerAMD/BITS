"""Assemble previously tested artifacts in cloud Linux; never rebuild them."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile


def metadata(path):
    return {'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Cloud Linux only')
    run_id, commit = os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_SHA']
    if not run_id.isdigit() or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Invalid source/run identifier')
    root = Path(__file__).resolve().parents[1] / '.api-security-results'
    evidence, release = root / 'evidence', root / 'release'
    packages = evidence / ('api-security-packages-' + run_id)
    release.mkdir()
    results = {}
    systems = ('rocky8', 'rocky9', 'rocky10', 'alma8', 'alma9', 'alma10',
               'debian11', 'debian12', 'debian13', 'ubuntu22', 'ubuntu24', 'ubuntu26')
    for system in systems:
        directory = evidence / ('api-security-' + system + '-' + run_id)
        result = json.loads((directory / 'validation.json').read_text())
        if (result['status'] != 'passed' or result['source_commit'] != commit or
                result['tests'] < 100 or any(result[k] for k in ('failures', 'errors', 'skipped')) or
                len(result['checks']) != 4):
            raise ValueError('Unverified security regression: ' + system)
        result['image_digests'] = json.loads((directory / 'image.json').read_text())
        if not result['image_digests'] or not (directory / 'tests.txt').stat().st_size:
            raise ValueError('Missing execution/image evidence: ' + system)
        results[system] = result
    manifest = json.loads((packages / 'manifest.json').read_text())
    artifacts = {}
    names = {
        'public_api': 'sckocp-api-0.4.0.tar.gz',
        'public_api_installer': 'sckocp-api-0.4.0.run',
        'bits_core/collector': 'mon-sensors-plugin-0.13.0.tar.gz',
        'mon_sensors_installer': 'mon-sensors-plugin-0.13.0.run',
    }
    for kind, name in names.items():
        item = manifest[kind]
        if item['file'] != name or metadata(packages / name) != {
                key: item[key] for key in ('bytes', 'sha256')}:
            raise ValueError('Built package mismatch: ' + kind)
        shutil.copyfile(str(packages / name), str(release / name))
        artifacts[name] = metadata(release / name)
    for name in ('SCKOCP-API.md', 'MON-SENSORS.md', 'API-SECURITY.md'):
        shutil.copyfile(str(packages / name), str(release / name))
    url = 'https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '/actions/runs/' + run_id
    write_json(release / 'VALIDATION.json', {'status': 'passed', 'source_commit': commit,
        'workflow': url, 'matrix': results,
        'scope': 'Isolated synthetic API/plugin regressions and actual old/new installer transitions',
        'production_hardware_tested': False})
    write_json(release / 'RELEASE.json', {'schema': 'sckocp-api-security-release-v1',
        'source_commit': commit, 'versions': {'sckocp-api': '0.4.0', 'mon-sensors-plugin': '0.13.0'},
        'artifacts': artifacts, 'workflow': url,
        'protocol': 'sckocp-api-v1, original v1 default',
        'standalone_managed_cli_integrity': True,
        'embedded_sdk_automatic_integrity': False,
        'original_sckocp_changes': False, 'network_listener': False,
        'production_deployed': False, 'publisher_signature': False})
    with tarfile.open(str(release / 'CLOUD-EVIDENCE.tar.gz'), 'w:gz') as archive:
        for system in systems:
            directory = evidence / ('api-security-' + system + '-' + run_id)
            for name in ('validation.json', 'image.json', 'tests.txt'):
                archive.add(str(directory / name), arcname=system + '/' + name)
    files = sorted(release.iterdir())
    (release / 'SHA256SUMS').write_text(''.join(
        metadata(path)['sha256'] + '  ' + path.name + '\n' for path in files), encoding='ascii')
    print(json.dumps({'source_commit': commit, 'systems_passed': len(results),
        'release_files': len(files) + 1, 'old_installer_fixture_distributed': False}))


if __name__ == '__main__':
    main()
