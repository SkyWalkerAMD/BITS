#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ "$(uname -s)" != Linux ]]; then
  echo 'Run this setup inside the cloud Linux environment.' >&2
  exit 1
fi

python3 -c 'import sys; assert sys.version_info >= (3, 11), "Cloud tests require Python 3.11+"'
python3 -m venv .cloud-venv
.cloud-venv/bin/python -m pip install -r requirements-test.txt
