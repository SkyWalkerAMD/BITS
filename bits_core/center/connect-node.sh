#!/bin/bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
for candidate in /usr/bin/python3 /usr/libexec/platform-python; do
    if [[ -x $candidate ]] && "$candidate" -I -B -c 'import sys;sys.exit(sys.version_info < (3,6))'; then
        exec "$candidate" -I -B -c 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("bits_core.center.node_connect",run_name="__main__")' "$source_dir" "$@"
    fi
done
echo 'Linux Python 3.6+ is required; the system Python will not be replaced' >&2
exit 1
