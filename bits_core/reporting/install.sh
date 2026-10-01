#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
report_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
for report_python in /usr/libexec/platform-python /usr/bin/python3.6 /usr/bin/python3; do
    if [[ -x $report_python ]] && "$report_python" -I -S -B -c 'import sys; sys.exit(sys.version_info[:2] != (3, 6))'; then
        exec "$report_python" -I -S -B "$report_source/install.py" "$@"
    fi
done
echo 'This package requires Linux x86_64 with an existing CPython 3.6 interpreter.' >&2
exit 1
