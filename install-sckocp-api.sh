#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SOURCE/installer-python.sh"
installer_python sckocp_api.install "$SOURCE" "$@"
