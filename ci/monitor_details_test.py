"""Cloud-only focused API/collector/report regression and synthetic preview."""
import json
import os
from pathlib import Path
import sys
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise SystemExit('Authorized cloud Linux only')
root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / 'tests')]
suite = unittest.TestSuite()
for pattern in ('test_sckocp*.py', 'test_mon_sensors.py'):
    suite.addTests(unittest.defaultTestLoader.discover(str(root / 'tests'), pattern=pattern))
result = unittest.TextTestRunner(verbosity=2).run(suite)
out = Path('/results')
out.mkdir(exist_ok=True)
(out / 'validation.json').write_text(json.dumps({'status': 'passed' if result.wasSuccessful() else 'failed',
    'source_commit': os.environ.get('GITHUB_SHA'), 'tests': result.testsRun,
    'errors': len(result.errors), 'failures': len(result.failures), 'python': sys.version,
    'scope': 'synthetic licensed output; no hardware or production access'}, indent=2))
if not result.wasSuccessful():
    raise SystemExit(1)
from test_sckocp_details import preview_record
from bits_core.batch.report_sheet import render
record = preview_record()
(out / 'report-preview.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
(out / 'report-preview.html').write_bytes(render(record))
