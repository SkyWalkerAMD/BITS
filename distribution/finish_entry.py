"""Choose the packaged streaming renderer without changing legacy node installs."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).absolute().parent
sys.path.insert(0, str(ROOT))
import common as distribution
distribution.verify('node')
target = Path(sys.argv.pop(1))
if target != Path('/var/lib/ocrun-node/app/mon-sensors-finish.d/finish.py'):
    raise SystemExit('Unexpected native finalizer path')
# Keep target in the real process argv for the existing PID/start-time stop check.
sys.path.insert(0, str(target.parent))
sys.modules.pop('common', None)
import finish
finish.REPORT = ROOT / 'mon-sensors-report'
try:
    raise SystemExit(finish.main())
except (OSError, ValueError, KeyError, TypeError) as error:
    print('mon-sensors-finish: ' + str(error), file=sys.stderr)
    raise SystemExit(1)
