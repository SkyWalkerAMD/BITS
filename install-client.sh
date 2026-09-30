#!/bin/bash
set -euo pipefail
umask 077
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CONFIG= TOOLS= TOOL_SHA= SKIP_DEPS=0 UPGRADE=0
while (($#)); do
    case "$1" in
        --config) CONFIG=${2:?}; shift 2 ;;
        --tools) TOOLS=${2:?}; shift 2 ;;
        --tools-sha256) TOOL_SHA=${2:?}; shift 2 ;;
        --skip-deps) SKIP_DEPS=1; shift ;;
        --upgrade) UPGRADE=1; shift ;;
        *) echo "Usage: $0 [--config HOST.json] [--tools tools.tar.gz --tools-sha256 HASH] [--skip-deps] [--upgrade]" >&2; exit 2 ;;
    esac
done
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
command -v flock >/dev/null || { echo 'Install util-linux (flock) first' >&2; exit 1; }
exec 9>/run/ocrun-install.lock
flock -n 9 || { echo 'Another OCRUN installation is in progress' >&2; exit 1; }
if (( UPGRADE )); then
    [[ -L /opt/ocrun/current && -f /etc/ocrun/agent.json ]] || { echo 'No complete client installation to upgrade' >&2; exit 1; }
    CONFIG=${CONFIG:-/etc/ocrun/agent.json}
else
    [[ ! -e /opt/ocrun/current && ! -L /opt/ocrun/current && ! -e /etc/ocrun/agent.json && ! -e /etc/systemd/system/ocrun-agent.service ]] || { echo 'Agent already installed; pause its queue and use --upgrade' >&2; exit 1; }
fi
[[ -r $CONFIG ]] || { echo 'A readable enrollment configuration is required' >&2; exit 2; }
for startup in /etc/rc.local /etc/rc.d/rc.local; do
    if [[ -f $startup ]] && grep -Eq '^[^#]*ocrun/(ocb|ocdog|oc-watchdog)' "$startup"; then
        echo "Disable the legacy OCRUN startup entry in $startup and stop its processes before installing." >&2
        exit 1
    fi
done
[[ -z $TOOLS || ( -f $TOOLS && $TOOL_SHA =~ ^[a-fA-F0-9]{64}$ ) ]] || { echo 'Tool archive requires its trusted SHA-256 digest' >&2; exit 2; }
if (( ! SKIP_DEPS )); then bash "$SOURCE/install-deps.sh"; fi
export PYTHONPATH="$SOURCE${PYTHONPATH:+:$PYTHONPATH}"
python3 - "$CONFIG" "$UPGRADE" <<'PY'
import os, sys
from ocrun.common import os_release, read_json, identifier
config = read_json(sys.argv[1])
identifier(config['host_id'])
if not os_release()['supported_family']:
    raise ValueError('Unsupported OS')
if not config['redis']['password'] or not config['upload']['password']:
    raise ValueError('Enrollment credentials are missing')
for name, default in [('state_dir', '/var/lib/ocrun-agent'), ('log_dir', '/var/log/ocrun')]:
    value = config.get(name, default)
    if not isinstance(value, str) or not os.path.isabs(value) or '\n' in value or '\r' in value:
        raise ValueError(name + ' must be an absolute path without newlines')
if sys.argv[2] == '1':
    old = read_json('/etc/ocrun/agent.json')
    for key in ['host_id', 'state_dir', 'log_dir']:
        if old.get(key) != config.get(key):
            raise ValueError('Upgrade cannot change ' + key)
PY
install -d -m 0755 /opt/ocrun/releases /opt/ocrun/tools
STAGING=$(mktemp -d /opt/ocrun/releases/.stage-0.12.8.XXXXXX)
RELEASE="/opt/ocrun/releases/0.12.8-${STAGING##*.}"
ACTIVATING=0 SUCCESS=0 WAS_ACTIVE=0 WAS_ENABLED=0 PREVIOUS=
rollback() {
    local outcome=$?
    trap - EXIT
    if (( ! SUCCESS && ACTIVATING )); then
        systemctl stop ocrun-agent >/dev/null 2>&1 || true
        if (( UPGRADE )); then
            ln -sfn -- "$PREVIOUS" /opt/ocrun/.current.rollback
            mv -Tf -- /opt/ocrun/.current.rollback /opt/ocrun/current
            cp -a -- "$STAGING/previous-agent.json" /etc/ocrun/agent.json
            cp -a -- "$STAGING/previous-agent.service" /etc/systemd/system/ocrun-agent.service
        else
            systemctl disable ocrun-agent >/dev/null 2>&1 || true
            rm -f -- /opt/ocrun/current /etc/ocrun/agent.json /etc/systemd/system/ocrun-agent.service
        fi
        systemctl daemon-reload || true
        flock -u 8 2>/dev/null || true
        if (( WAS_ACTIVE )); then systemctl start ocrun-agent || true; fi
        echo 'Activation failed; previous installation restored. Logs and state were retained.' >&2
    fi
    rm -f -- /opt/ocrun/.current.install /opt/ocrun/.current.rollback
    if [[ -n ${STAGING:-} && $STAGING == /opt/ocrun/releases/.stage-0.12.8.* ]]; then rm -rf -- "$STAGING"; fi
    if (( ! SUCCESS )) && [[ $RELEASE == /opt/ocrun/releases/0.12.8-* ]]; then rm -rf -- "$RELEASE"; fi
    exit "$outcome"
}
trap rollback EXIT
mkdir "$STAGING/runtime"
cp -a "$SOURCE/ocrun" "$SOURCE/sckocp_api" "$SOURCE/mon_sensors_plugin" "$STAGING/runtime/"
install -m 0755 "$SOURCE/mon-sensors-plugin" "$STAGING/runtime/mon-sensors-plugin"
install -m 0755 "$SOURCE/sckocp-api" "$STAGING/runtime/sckocp-api"
install -m 0600 "$CONFIG" "$STAGING/agent.json"
if [[ -n $TOOLS ]]; then
    python3 -m ocrun.unpack "$TOOLS" "$STAGING/runtime/tools" --sha256 "$TOOL_SHA"
    python3 - "$STAGING/agent.json" "$RELEASE/tools" <<'PY'
import sys
from ocrun.common import read_json, write_json
config = read_json(sys.argv[1])
config['tool_root'] = sys.argv[2]
write_json(sys.argv[1], config)
PY
fi
# Staging above has not changed the live installation.
if (( UPGRADE )); then
    python3 - /etc/ocrun/agent.json <<'PY'
import os, sys
from ocrun.common import read_json
from ocrun.queue import Queue
from ocrun.rediswire import Redis
config = read_json(sys.argv[1])
queue = Queue(Redis(config['redis']), config['host_id'])
if not queue.is_paused():
    raise ValueError('Pause the device queue on the center before upgrading')
if queue.running() or os.path.exists(os.path.join(config.get('state_dir', '/var/lib/ocrun-agent'), 'active.json')):
    raise ValueError('Wait for the current task and its result acknowledgement before upgrading')
PY
    PREVIOUS=$(readlink /opt/ocrun/current)
    cp -a /etc/ocrun/agent.json "$STAGING/previous-agent.json"
    cp -a /etc/systemd/system/ocrun-agent.service "$STAGING/previous-agent.service"
    systemctl is-active --quiet ocrun-agent && WAS_ACTIVE=1
    systemctl is-enabled --quiet ocrun-agent && WAS_ENABLED=1
    ACTIVATING=1
    systemctl stop ocrun-agent
fi
STATE=$(python3 -c 'import sys; from ocrun.common import read_json; print(read_json(sys.argv[1]).get("state_dir", "/var/lib/ocrun-agent"))' "$CONFIG")
LOGS=$(python3 -c 'import sys; from ocrun.common import read_json; print(read_json(sys.argv[1]).get("log_dir", "/var/log/ocrun"))' "$CONFIG")
install -d -m 0700 /etc/ocrun "$STATE" "$LOGS"
exec 8>"$STATE/agent.lock"
flock -n 8 || { echo 'Another agent still owns the device; activation refused' >&2; exit 1; }
[[ ! -e "$STATE/active.json" ]] || { echo 'Unacknowledged task state remains; activation refused' >&2; exit 1; }
mv -- "$STAGING/runtime" "$RELEASE"
ACTIVATING=1
install -m 0600 "$STAGING/agent.json" /etc/ocrun/agent.json
ln -sfn -- "$RELEASE" /opt/ocrun/.current.install
mv -Tf -- /opt/ocrun/.current.install /opt/ocrun/current
install -m 0644 "$SOURCE/systemd/ocrun-agent.service" /etc/systemd/system/ocrun-agent.service
flock -u 8
systemctl daemon-reload
if (( ! UPGRADE || WAS_ENABLED )); then systemctl enable ocrun-agent; fi
if (( ! UPGRADE || WAS_ACTIVE )); then
    systemctl start ocrun-agent
    sleep 2
    systemctl is-active --quiet ocrun-agent
fi
if (( UPGRADE )); then
    install -d -m 0700 "$RELEASE/rollback"
    install -m 0600 "$STAGING/previous-agent.json" "$RELEASE/rollback/agent.json"
    install -m 0600 "$STAGING/previous-agent.service" "$RELEASE/rollback/ocrun-agent.service"
    printf '%s\n' "$PREVIOUS" > "$RELEASE/rollback/previous-runtime.txt"
fi
SUCCESS=1
if (( UPGRADE )); then
    echo "Upgrade activated. Previous runtime retained at $PREVIOUS; rollback configuration in $RELEASE/rollback. Device queue remains paused."
else
    echo 'Agent installed. Check collection before submitting hardware tests:'
fi
echo 'sudo env PYTHONPATH=/opt/ocrun/current python3 -m ocrun.agent --check'
