#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
mode='' skip_deps=0 address='' network=''
arguments=("$@")
while (( $# )); do
    case "$1" in
        --check|--apply|--rollback) [[ -z $mode ]] || { echo 'Choose exactly one installer action' >&2; exit 2; }; mode=$1; shift ;;
        --skip-deps) skip_deps=1; shift ;;
        --address|--network)
            (( $# >= 2 )) || { echo "Missing value: $1" >&2; exit 2; }
            if [[ $1 == --address ]]; then address=$2; else network=$2; fi
            shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ -n $mode ]] || { echo 'Usage: bash bits_core/center/install.sh --address IP --network CIDR --check|--apply [--skip-deps], or --rollback' >&2; exit 2; }
[[ $EUID == 0 ]] || { echo 'Run the server installer as root' >&2; exit 1; }
if [[ $mode != --rollback ]]; then
    [[ $address =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && $network =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/([89]|[12][0-9]|3[0-2])$ ]] || { echo 'Supply --address IPv4 and --network a bounded CIDR (/8 to /32)' >&2; exit 2; }
fi
bash "$source_dir/bits_core/center/dependencies.sh" --check
if [[ $mode == --apply && $skip_deps == 0 && ! -e /etc/bits/center/manifest.json ]]; then
    bash "$source_dir/bits_core/center/dependencies.sh" --install
fi
python=''
for candidate in /usr/bin/python3 /usr/libexec/platform-python; do
    if [[ -x $candidate ]] && "$candidate" -I -B -c 'import sys;sys.exit(sys.version_info < (3,6))'; then python=$candidate; break; fi
done
[[ -n $python ]] || { echo 'Python 3.6+ is missing. --apply installs the native Python package; --check never installs dependencies.' >&2; exit 1; }
exec "$python" -I -B -c 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("bits_core.center.install",run_name="__main__")' "$source_dir" --source "$source_dir" "${arguments[@]}"
