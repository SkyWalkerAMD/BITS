#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ "$(uname -s)" != Linux ]]; then
  echo 'Run validation inside the cloud Linux environment.' >&2
  exit 1
fi

if [[ ! -x .cloud-venv/bin/python ]]; then
  echo 'First run: bash ci/setup.sh' >&2
  exit 1
fi

mkdir -p .cloud-results
{
  date -u +'%Y-%m-%dT%H:%M:%SZ'
  git rev-parse HEAD
  cat /etc/os-release
  uname -srmo
  .cloud-venv/bin/python --version
  .cloud-venv/bin/python -m pip freeze
} > .cloud-results/environment.txt

.cloud-venv/bin/python tests/run.py --fail-on-skip --report-json .cloud-results/summary.json 2>&1 | tee .cloud-results/tests.txt
.cloud-venv/bin/python tests/check_shell.py 2>&1 | tee .cloud-results/syntax.txt
printf 'Cloud logic and Linux process tests completed. Real deployment recovery is validated in the separate services/installation jobs; hardware is not validated.\n'
