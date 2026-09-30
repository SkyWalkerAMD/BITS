"""Give the disposable runner's hosted Python production-like path permissions."""
import json
import os
from pathlib import Path
import stat
import sys

if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0:
    raise SystemExit("Only run in the disposable root GitHub Actions fixture")

interpreter = Path(sys.argv[1]).resolve(strict=True)
if not str(interpreter).startswith("/opt/hostedtoolcache/Python/"):
    raise SystemExit("Unexpected hosted Python path: " + str(interpreter))

# setup-python intentionally uses a shared writable tool-cache. The installer
# must refuse such paths in production. Keep that policy and make only this
# VM's selected interpreter ancestry non-writable by other users for the tests.
changes = []
for path in list(reversed(interpreter.parents)) + [interpreter]:
    if path == Path("/"):
        continue
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) or path == interpreter and stat.S_ISREG(info.st_mode)):
        raise SystemExit("Unexpected path type: " + str(path))
    mode = stat.S_IMODE(info.st_mode)
    changes.append({"path": str(path), "uid": info.st_uid, "gid": info.st_gid,
                    "before": oct(mode), "after": oct(mode & ~0o022)})
    if mode & 0o022:
        path.chmod(mode & ~0o022)
print(json.dumps({"fixture_only": True, "paths": changes}, indent=2))
