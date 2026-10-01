#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
finish_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
for finish_python in /usr/libexec/platform-python /usr/bin/python3 /usr/local/bin/python3; do
    if [[ -x $finish_python ]] && "$finish_python" -I -S -B -c 'import sys; sys.exit(sys.version_info < (3, 6))'; then
        exec "$finish_python" -I -S -B "$finish_source/install.py" "$@"
    fi
done
echo 'An existing Linux Python 3.6+ interpreter is required.' >&2
exit 1
