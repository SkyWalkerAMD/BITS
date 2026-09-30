#!/bin/bash
set -euo pipefail
umask 077
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ADDRESS= NETWORK= STAGING= SKIP_DEPS=0
while (($#)); do
    case "$1" in
        --address) ADDRESS=${2:?}; shift 2 ;;
        --network) NETWORK=${2:?}; shift 2 ;;
        --render-only) STAGING=${2:?}; shift 2 ;;
        --skip-deps) SKIP_DEPS=1; shift ;;
        *) echo "Usage: $0 --address SERVER_IP --network CLIENT_CIDR [--render-only DIRECTORY] [--skip-deps]" >&2; exit 2 ;;
    esac
done
[[ -n $ADDRESS && -n $NETWORK ]] || { echo 'Server IP and client network are required' >&2; exit 2; }
export PYTHONPATH="$SOURCE${PYTHONPATH:+:$PYTHONPATH}"
if [[ -n $STAGING ]]; then
    python3 -m ocrun.provision --root "$STAGING" --address "$ADDRESS" --network "$NETWORK"
    exit
fi
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 22.04 ]] || { echo 'Server installer targets Ubuntu 22.04' >&2; exit 1; }
exec 9>/run/ocrun-install.lock
flock -n 9 || { echo 'Another OCRUN installation is in progress' >&2; exit 1; }
FILES=(/etc/ocrun/server.json /etc/ocrun/rsync.secrets /etc/ocrun/redis.conf /etc/ocrun/rsyncd.conf /etc/nginx/conf.d/ocrun.conf /etc/systemd/system/ocrun-redis.service /etc/systemd/system/ocrun-rsync.service /etc/systemd/system/ocrun-log-verifier.service /usr/local/bin/ocrun-admin)
for target in /opt/ocrun/current /etc/ocrun/agent.json "${FILES[@]}"; do
    [[ ! -e $target && ! -L $target ]] || { echo "Existing installation file: $target; refusing to replace configuration or credentials" >&2; exit 1; }
done
# Keep the prepared credentials across failed attempts; active files can be rolled back.
install -d -m 0700 /var/lib/ocrun-install
STAGING=/var/lib/ocrun-install/server
if [[ ! -d $STAGING ]]; then
    candidate=$(mktemp -d /var/lib/ocrun-install/.server.XXXXXX)
    python3 -m ocrun.provision --root "$candidate" --address "$ADDRESS" --network "$NETWORK"
    mv -- "$candidate" "$STAGING"
else
    python3 - "$STAGING/etc/ocrun/server.json" "$ADDRESS" "$NETWORK" <<'PY'
import ipaddress, sys
from ocrun.common import read_json
previous = read_json(sys.argv[1])
if previous['address'] != str(ipaddress.IPv4Address(sys.argv[2])) or previous['network'] != str(ipaddress.IPv4Network(sys.argv[3], strict=False)):
    raise ValueError('Retry must use the same server address and network as the prepared installation')
PY
fi
if (( ! SKIP_DEPS )); then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 redis-server rsync nginx
fi
for dependency in python3 redis-server rsync nginx; do
    command -v "$dependency" >/dev/null || { echo "Missing dependency: $dependency" >&2; exit 1; }
done
getent passwd redis >/dev/null || { echo 'The redis service account is missing; install the Redis system package' >&2; exit 1; }
getent passwd ocrun-logs >/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin ocrun-logs
install -d -m 0755 /opt/ocrun/releases /srv/ocrun/public/releases /srv/ocrun/public/tools /srv/ocrun/logs
RELEASE=$(mktemp -d /opt/ocrun/releases/0.12.8-server.XXXXXX)
ACTIVATING=0 SUCCESS=0
rollback() {
    local outcome=$?
    trap - EXIT
    if (( ! SUCCESS && ACTIVATING )); then
        systemctl disable --now ocrun-log-verifier ocrun-rsync ocrun-redis >/dev/null 2>&1 || true
        rm -f -- "${FILES[@]}" /opt/ocrun/current
        systemctl daemon-reload || true
        systemctl reload nginx >/dev/null 2>&1 || true
        echo "Installation activation failed. Data and prepared credentials remain; rerun this installer with the same address/network." >&2
    fi
    if (( ! SUCCESS )) && [[ $RELEASE == /opt/ocrun/releases/0.12.8-server.* ]]; then rm -rf -- "$RELEASE"; fi
    exit "$outcome"
}
trap rollback EXIT
cp -a "$SOURCE/ocrun" "$SOURCE/sckocp_api" "$SOURCE/mon_sensors_plugin" "$RELEASE/"
# Prepare the downloadable release before any live service is changed.
tar -czf "$STAGING/ocrun-client-0.12.8.tar.gz" --exclude='__pycache__' -C "$SOURCE" ocrun sckocp_api mon_sensors_plugin mon-sensors-plugin sckocp-api install-client.sh install-deps.sh install-mon-sensors-plugin.sh install-sckocp-api.sh installer-python.sh systemd README.md FLEET.md OPERATIONS.md SCKOCP.md SCKOCP-API.md MON-SENSORS.md PACKAGES.md
install -d -m 0700 /etc/ocrun /etc/ocrun/enrollments
install -d -o redis -g redis -m 0750 /var/lib/ocrun-redis
ACTIVATING=1
ln -s "$RELEASE" /opt/ocrun/current
install -m 0600 "$STAGING/etc/ocrun/server.json" /etc/ocrun/server.json
install -m 0600 "$STAGING/etc/ocrun/rsync.secrets" /etc/ocrun/rsync.secrets
install -m 0644 "$STAGING/etc/ocrun/redis.conf" /etc/ocrun/redis.conf
chmod 0755 /etc/ocrun
install -o redis -g redis -m 0600 "$STAGING/var/lib/ocrun-redis/users.acl" /var/lib/ocrun-redis/users.acl
install -m 0644 "$STAGING/etc/ocrun/rsyncd.conf" /etc/ocrun/rsyncd.conf
install -m 0644 "$STAGING/etc/nginx/conf.d/ocrun.conf" /etc/nginx/conf.d/ocrun.conf
install -m 0644 "$STAGING/etc/systemd/system/ocrun-redis.service" /etc/systemd/system/ocrun-redis.service
install -m 0644 "$STAGING/etc/systemd/system/ocrun-rsync.service" /etc/systemd/system/ocrun-rsync.service
install -m 0644 "$SOURCE/systemd/ocrun-log-verifier.service" /etc/systemd/system/ocrun-log-verifier.service
cat > /usr/local/bin/ocrun-admin <<'EOF'
#!/bin/bash
export PYTHONPATH=/opt/ocrun/current
exec python3 -m ocrun.admin "$@"
EOF
chmod 0755 /usr/local/bin/ocrun-admin
nginx -t
systemctl daemon-reload
systemctl enable --now ocrun-redis ocrun-rsync ocrun-log-verifier nginx
systemctl reload nginx
sleep 2
for service in ocrun-redis ocrun-rsync ocrun-log-verifier nginx; do systemctl is-active --quiet "$service"; done
install -m 0644 "$STAGING/ocrun-client-0.12.8.tar.gz" /srv/ocrun/public/releases/ocrun-client-0.12.8.tar.gz
SUCCESS=1
rm -rf -- /var/lib/ocrun-install/server
echo "Installed. Enroll a host with: sudo ocrun-admin enroll HOST_ID --output HOST_ID.json"
echo "Management services: $ADDRESS TCP 6380 (Redis), 1873 (log upload), 8080 (downloads)."
echo "Restrict access to $NETWORK using the server's existing firewall. No firewall rules were changed."
