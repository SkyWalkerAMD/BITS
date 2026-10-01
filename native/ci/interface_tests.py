"""Source regression of the data-only boundary, in disposable Linux only."""
import json
import os
from pathlib import Path
import sys
import unittest

assert os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "linux"
root = Path("/src")
sys.path[:0] = [str(root), str(root / "tests")]
suite = unittest.TestSuite()
for name in ("test_sckocp_public_api", "test_sckocp_security", "test_sckocp_details"):
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
result = unittest.TextTestRunner(verbosity=2).run(suite)
Path("/results/interface-tests.json").write_text(json.dumps({
    "tests": result.testsRun, "errors": len(result.errors), "failures": len(result.failures),
    "status": "passed" if result.wasSuccessful() else "failed", "python": sys.version,
    "scope": "fixed data reads, native gate denial, Primary filtering, trusted executable identity"}))
sys.exit(0 if result.wasSuccessful() else 1)
