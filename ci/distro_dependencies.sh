#!/bin/bash
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true && -f /.dockerenv ]] || { echo 'Run only inside a disposable cloud CI container' >&2; exit 1; }
source /etc/os-release
if [[ $ID == centos && $VERSION_ID == 7 ]]; then
    # Explicit test fixture for the archived target OS. Production install-deps.sh
    # never changes customer repositories or disables signature verification.
    cat > /etc/yum.repos.d/ocrun-ci-vault.repo <<'EOF'
[ocrun-ci-base]
name=OCRUN CI CentOS 7.9 archived base
baseurl=https://vault.centos.org/7.9.2009/os/$basearch/
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
[ocrun-ci-updates]
name=OCRUN CI CentOS 7.9 archived updates
baseurl=https://vault.centos.org/7.9.2009/updates/$basearch/
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
[ocrun-ci-extras]
name=OCRUN CI CentOS 7.9 archived extras
baseurl=https://vault.centos.org/7.9.2009/extras/$basearch/
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
EOF
    mkdir -p /tmp/ocrun-ci-bin
    cat > /tmp/ocrun-ci-bin/yum <<'EOF'
#!/bin/bash
exec /usr/bin/yum --disablerepo='*' --enablerepo='ocrun-ci-*' "$@"
EOF
    chmod 0755 /tmp/ocrun-ci-bin/yum
    export PATH="/tmp/ocrun-ci-bin:$PATH"
fi
bash install-deps.sh
python3 - <<'PY'
import importlib, json, pathlib, sys
from ocrun.common import os_release
for path in pathlib.Path('ocrun').glob('*.py'):
    importlib.import_module('ocrun.' + path.stem)
for path in pathlib.Path('sckocp_api').glob('*.py'):
    if path.stem != '__main__':
        importlib.import_module('sckocp_api.' + path.stem)
assert os_release()['supported_family']
print(json.dumps({'successful': True, 'os': os_release(), 'python': sys.version,
                  'coverage': 'dependency installation and all runtime imports; shared host kernel, no systemd/hardware acceptance'}))
PY
