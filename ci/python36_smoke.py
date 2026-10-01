"""Actual Python 3.6 stdlib runtime smoke; no hardware, network or third-party packages."""
import hashlib
import importlib
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
assert sys.version_info[:2] == (3, 6), sys.version
for path in sorted((ROOT / 'ocrun').glob('*.py')):
    importlib.import_module('ocrun.' + path.stem)
for path in sorted((ROOT / 'sckocp_api').glob('*.py')):
    if path.stem != '__main__':
        importlib.import_module('sckocp_api.' + path.stem)
for path in sorted((ROOT / 'bits_core/collector').glob('*.py')):
    importlib.import_module('bits_core.collector.' + path.stem)
from ocrun.common import identifier, read_json, write_json
from ocrun.rediswire import Redis
from ocrun.unpack import unpack
from ocrun.workloads import validate_task
from ocrun.provision import render_server

assert identifier('centos79-node') == 'centos79-node'
assert Redis.decode(io.BytesIO(b'*2\r\n$3\r\nfoo\r\n:42\r\n')) == ['foo', 42]
validate_task({'id': 'smoke', 'tool': 'cpu-burn', 'threads': 1, 'duration_seconds': 3})
with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    write_json(str(root / 'state.json'), {'version': sys.version})
    assert read_json(str(root / 'state.json'))['version'] == sys.version
    render_server(str(root / 'rendered'), '192.0.2.1', '192.0.2.0/24')
    archive = root / 'tools.tar.gz'
    with tarfile.open(str(archive), 'w:gz') as bundle:
        entry = tarfile.TarInfo('test.txt')
        entry.size = 4
        bundle.addfile(entry, io.BytesIO(b'test'))
    unpack(str(archive), str(root / 'tools'), hashlib.sha256(archive.read_bytes()).hexdigest())
    assert (root / 'tools/test.txt').read_bytes() == b'test'
suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_sckocp*.py')
suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_mon_sensors.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_plugin*.py'))
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and result.testsRun > 0 and not result.skipped
print(json.dumps({'successful': True, 'python': sys.version, 'sckocp_tests': result.testsRun,
                  'coverage': 'all runtime imports, RESP, task validation, state persistence, server rendering, archive extraction, sckocp adapter and collector'}))
