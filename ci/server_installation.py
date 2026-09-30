"""Verify center activation rollback and credential-preserving retry in cloud only."""
import json
import os
import pathlib
import subprocess
import sys

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
    raise SystemExit('Requires root on a disposable GitHub Actions runner')
os.umask(0o077)
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocrun.common import read_json
from ocrun.provision import render_server

address = sys.argv[1]
network = address + '/32'
staging = pathlib.Path('/var/lib/ocrun-install/server')
config = render_server(str(staging), address, network)
nginx = staging / 'etc/nginx/conf.d/ocrun.conf'
valid = nginx.read_text()
nginx.write_text('this_is_an_intentionally_invalid_nginx_directive;\n')
command = ['bash', str(ROOT / 'install-server.sh'), '--skip-deps', '--address', address, '--network', network]
failed = subprocess.run(command)
assert failed.returncode != 0, 'Invalid service configuration must fail activation'
assert not pathlib.Path('/etc/ocrun/server.json').exists()
assert not pathlib.Path('/opt/ocrun/current').exists()
assert not pathlib.Path('/etc/nginx/conf.d/ocrun.conf').exists()
assert read_json(str(staging / 'etc/ocrun/server.json'))['redis']['password'] == config['redis']['password']
nginx.write_text(valid)
subprocess.check_call(command)
assert read_json('/etc/ocrun/server.json')['redis']['password'] == config['redis']['password']
assert not staging.exists()
summary = {'successful': True, 'checks': ['invalid nginx activation rolls back live files',
           'prepared credentials survive failure', 'corrected retry activates services with original credentials']}
report = ROOT / '.cloud-results/server-installation.json'
report.write_text(json.dumps(summary, indent=2) + '\n')
report.chmod(0o644)
print(json.dumps(summary))
