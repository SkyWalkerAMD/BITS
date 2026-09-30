"""Use locally installed test dependencies without modifying system Python."""
import argparse
import json
import pathlib
import sys
import tempfile
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--fail-on-skip', action='store_true')
parser.add_argument('--report-json', type=pathlib.Path)
args = parser.parse_args()

root = pathlib.Path(__file__).resolve().parents[1]
temporary_root = root / '.testtmp'
temporary_root.mkdir(exist_ok=True)
tempfile.tempdir = str(temporary_root)
sys.path[:0] = [str(root), str(root / '.testdeps')]
suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
successful = (result.wasSuccessful() and result.testsRun > 0 and
              not (args.fail_on_skip and result.skipped))
summary = {
    'tests_run': result.testsRun,
    'passed': (result.testsRun - len(result.failures) - len(result.errors) -
               len(result.skipped) - len(result.expectedFailures) -
               len(result.unexpectedSuccesses)),
    'failures': len(result.failures),
    'errors': len(result.errors),
    'skipped': len(result.skipped),
    'expected_failures': len(result.expectedFailures),
    'unexpected_successes': len(result.unexpectedSuccesses),
    'successful': successful,
    'skipped_tests': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
}
if args.report_json:
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
sys.exit(0 if successful else 1)
