"""Real 0.1.0 through 0.2.4 upgrade and rollback."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SRC = Path('/src')


def command(*args, **kwargs):
    result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    if kwargs.get('good', True) and result.returncode:
        raise AssertionError(result.stderr.decode('utf-8', 'replace'))
    return result


def main():
    app = Path('/root/ocrun')
    (app / 'py').mkdir(parents=True)
    upstream = SRC / 'integrations/mon-sensors/upstream-0.9.24a'
    for n in ('ocb', 'oct', 'mon-sensors'):
        shutil.copyfile(str(upstream / n), str(app / n))
        (app / n).chmod(0o755)
    shutil.copyfile(str(upstream / 'mon-analyse-log.py'), str(app / 'py/mon-analyse-log.py'))
    (app / 'mon-analyse-log').symlink_to('py/mon-analyse-log.py')
    command('/bin/bash', SRC / '.previous021/dist/mon-sensors-plugin-0.12.9.run', '--app', app, '--backend', 'sckocp')
    old_report = SRC / '.baseline/report-dist/mon-sensors-report-py36-0.1.1.run'
    old_finish = SRC / '.baseline/finish-dist/mon-sensors-finish-0.1.0.run'
    new_report = SRC / 'report-dist/mon-sensors-report-py36-0.2.0.run'
    new_finish = SRC / '.previous020/finish-dist/mon-sensors-finish-0.2.0.run'
    corrected_finish = SRC / '.previous021/finish-dist/mon-sensors-finish-0.2.1.run'
    authenticated_finish = SRC / '.previous022/finish-dist/mon-sensors-finish-0.2.2.run'
    suite_finish = SRC / '.previous023/finish-dist/mon-sensors-finish-0.2.3.run'
    mlc_finish = SRC / '.previous024/finish-dist/mon-sensors-finish-0.2.4.run'
    report_finish = SRC / 'finish-dist/mon-sensors-finish-0.2.5.run'
    command('/bin/bash', old_report, '--app', app)
    command('/bin/bash', old_finish, '--app', app)
    original = {n: (app / n).read_bytes() for n in ('ocb', 'oct', 'mon-sensors-finish')}
    old_launcher = Path('/usr/local/bin/mon-sensors-report').read_bytes()
    bundle = Path('/root/update-fixture')
    bundle.mkdir()
    for source in (new_report, new_finish, SRC / 'finish_addon/update-node.sh'):
        shutil.copyfile(str(source), str(bundle / source.name))
    (bundle / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
        for p in sorted(bundle.iterdir())))
    command('/bin/bash', bundle / 'update-node.sh', '--check')
    assert (app / 'ocb').read_bytes() == original['ocb']
    command('/bin/bash', bundle / 'update-node.sh', '--apply')
    command(app / 'mon-sensors-finish', 'check', '--scheduler')
    command('/bin/bash', bundle / 'update-node.sh', '--rollback')
    for n, data in original.items():
        assert data == (app / n).read_bytes(), n
    assert Path('/usr/local/bin/mon-sensors-report').read_bytes() == old_launcher
    command(app / 'mon-sensors-finish', 'check')
    command('/bin/bash', bundle / 'update-node.sh', '--apply')
    command(app / 'mon-sensors-finish', 'check')
    previous_names = ['ocb', 'oct', 'mon-sensors-finish', '.mon-sensors-finish-install.json']
    previous_names += [str(p.relative_to(app)) for p in (app / 'mon-sensors-finish.d').iterdir() if p.is_file()]
    previous_files = {name: (app / name).read_bytes() for name in previous_names}
    report_launcher = Path('/usr/local/bin/mon-sensors-report').read_bytes()
    command('/bin/bash', corrected_finish, '--app', app, '--check')
    assert all((app / name).read_bytes() == data for name, data in previous_files.items())
    command('/bin/bash', corrected_finish, '--app', app)
    assert b'0.2.1' in command(app / 'mon-sensors-finish', '--version').stdout
    command(app / 'mon-sensors-finish', 'check', '--scheduler')
    command('/bin/bash', corrected_finish, '--app', app, '--rollback', '--check')
    command('/bin/bash', corrected_finish, '--app', app, '--rollback')
    assert all((app / name).read_bytes() == data for name, data in previous_files.items())
    assert report_launcher == Path('/usr/local/bin/mon-sensors-report').read_bytes()
    assert b'0.2.0' in command(app / 'mon-sensors-finish', '--version').stdout
    command(app / 'mon-sensors-finish', 'check')
    command('/bin/bash', corrected_finish, '--app', app)
    previous_021 = {name: (app / name).read_bytes() for name in previous_names}
    command('/bin/bash', authenticated_finish, '--app', app, '--check')
    assert all((app / name).read_bytes() == data for name, data in previous_021.items())
    command('/bin/bash', authenticated_finish, '--app', app)
    assert b'0.2.2' in command(app / 'mon-sensors-finish', '--version').stdout
    command('/bin/bash', authenticated_finish, '--app', app, '--rollback')
    assert all((app / name).read_bytes() == data for name, data in previous_021.items())
    command('/bin/bash', authenticated_finish, '--app', app)
    previous_022 = {name: (app / name).read_bytes() for name in previous_names}
    command('/bin/bash', suite_finish, '--app', app, '--check')
    assert all((app / name).read_bytes() == data for name, data in previous_022.items())
    command('/bin/bash', suite_finish, '--app', app)
    hooks = {n: (app / n).read_bytes() for n in ('ocb', 'oct', 'mon-sensors', 'py/mon-analyse-log.py')}
    artifact = json.loads((SRC / 'dist/manifest.json').read_text())['mon_sensors_installer']
    assert Path(artifact['file']).name == artifact['file']
    plugin = SRC / 'dist' / artifact['file']
    assert hashlib.sha256(plugin.read_bytes()).hexdigest() == artifact['sha256']
    command('/bin/bash', plugin, '--app', app, '--modules-only', '--check')
    command('/bin/bash', plugin, '--app', app, '--modules-only')
    assert all((app / n).read_bytes() == data for n, data in hooks.items())
    command(app / 'mon-sensors-finish', 'check', '--scheduler')
    assert b'0.2.3' in command(app / 'mon-sensors-finish', '--version').stdout
    command('/bin/bash', suite_finish, '--app', app, '--rollback')
    assert all((app / name).read_bytes() == data for name, data in previous_022.items())
    command('/bin/bash', suite_finish, '--app', app)
    previous_023_names = previous_names + ['mon-sensors-finish.d/suite.py']
    previous_023 = {name: (app / name).read_bytes() for name in previous_023_names}
    command('/bin/bash', mlc_finish, '--app', app, '--check')
    assert all((app / name).read_bytes() == data for name, data in previous_023.items())
    command('/bin/bash', mlc_finish, '--app', app)
    assert b'0.2.4' in command(app / 'mon-sensors-finish', '--version').stdout
    command(app / 'mon-sensors-finish', 'check', '--scheduler')
    command('/bin/bash', mlc_finish, '--app', app, '--rollback')
    assert all((app / name).read_bytes() == data for name, data in previous_023.items())
    command('/bin/bash', mlc_finish, '--app', app)
    previous_024 = {name: (app / name).read_bytes() for name in previous_023_names}
    command('/bin/bash', report_finish, '--app', app, '--check')
    assert all((app / name).read_bytes() == data for name, data in previous_024.items())
    command('/bin/bash', report_finish, '--app', app)
    assert b'0.2.5' in command(app / 'mon-sensors-finish', '--version').stdout
    command(app / 'mon-sensors-finish', 'check', '--scheduler')
    command('/bin/bash', report_finish, '--app', app, '--rollback')
    assert all((app / name).read_bytes() == data for name, data in previous_024.items())
    assert not (app / 'mon-sensors-finish.d/report_sheet.py').exists()
    command('/bin/bash', report_finish, '--app', app)
    # A real local edit must be retained and rejected, never silently repaired.
    with (app / 'oct').open('a') as stream:
        stream.write('# locally modified\n')
    rejected = command('/bin/bash', report_finish, '--app', app, '--check', good=False)
    assert rejected.returncode != 0
    assert (app / 'oct').read_text().endswith('# locally modified\n')
    result = {'status': 'passed', 'checks': ['baseline_install', 'upgrade_preflight_read_only',
        'report_upgrade', 'finish_upgrade', 'finish_rollback_exact_bytes', 'report_rollback_exact_bytes',
        'old_release_runs', 'reupgrade', '020_to_021_preflight_read_only', '020_to_021_upgrade',
        '021_to_020_rollback_all_managed_bytes', 'report_020_unchanged_by_patch',
        '020_runs_after_rollback', '021_reupgrade', '021_to_022_preflight_read_only', '021_to_022_upgrade',
        '022_to_021_rollback_all_managed_bytes', '022_reupgrade', '022_to_023_preflight_read_only',
        '022_to_023_upgrade', '023_to_022_rollback_all_managed_bytes', '023_reupgrade',
        '023_to_024_preflight_read_only', '023_to_024_upgrade', '024_to_023_rollback_all_managed_bytes',
        '024_reupgrade', '024_to_025_upgrade_readonly_preflight', '025_to_024_exact_rollback_and_new_helper_removed',
        '025_reupgrade', 'plugin_0129_to_01210_preserves_finish_and_report_hooks', 'modified_oct_refused']}
    Path('/results/upgrade.json').write_text(json.dumps(result, indent=2) + '\n')
    os.chmod('/results/upgrade.json', 0o644)
    print(json.dumps(result))


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Cloud container only')
    os.umask(0o077)
    main()
