#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SOURCE/installer-python.sh"
# Retain the original positional application-directory syntax.
if [[ $# -gt 0 && $1 != -* ]]; then
    set -- --app "$1" "${@:2}"
fi
installer_python bits_core.collector.install "$SOURCE" "$@"
