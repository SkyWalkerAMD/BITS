#!/bin/bash
set -euo pipefail
umask 022
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
for control_python in /usr/libexec/platform-python /usr/bin/python3; do
    if [[ -x $control_python ]] && "$control_python" -I -B -c 'import sys;sys.exit(sys.version_info < (3,6))'; then
        exec "$control_python" -I -B "$source_dir/install.py" "$@"
    fi
done
echo 'Requires system Python 3.6+; no interpreter or dependencies were installed.' >&2
exit 2
