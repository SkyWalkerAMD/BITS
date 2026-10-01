#!/bin/bash
# Only installs distribution packages. Never rewrites repositories or OS Python.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
mode=${1:---check}
[[ $mode == --check || $mode == --install ]] || { echo 'Usage: dependencies.sh [--check|--install]' >&2; exit 2; }
source /etc/os-release
packages=(python3 nginx rsync ca-certificates tar gzip util-linux nftables)
module=''
case "$ID:${VERSION_ID%%.*}" in
    rocky:8|almalinux:8|rhel:8) manager=dnf; module=redis:6; packages+=(redis iproute policycoreutils-python-utils selinux-policy-targeted) ;;
    rocky:9|almalinux:9|rhel:9) manager=dnf; packages+=(redis iproute policycoreutils-python-utils selinux-policy-targeted) ;;
    rocky:10|almalinux:10|rhel:10) manager=dnf; packages+=(valkey iproute policycoreutils-python-utils selinux-policy-targeted) ;;
    ubuntu:22|ubuntu:24|ubuntu:26)
        [[ $VERSION_ID == 22.04 || $VERSION_ID == 24.04 || $VERSION_ID == 26.04 ]] || exit 2
        manager=apt-get; packages+=(redis-server redis-tools iproute2) ;;
    debian:11|debian:12|debian:13) manager=apt-get; packages+=(redis-server redis-tools iproute2) ;;
    *) echo "Unsupported server system: $ID $VERSION_ID" >&2; exit 2 ;;
esac
printf 'Server OS: %s %s; package manager: %s\n' "$ID" "$VERSION_ID" "$manager"
printf 'Packages:'; printf ' %s' "${packages[@]}"; printf '\n'
[[ -z $module ]] || printf 'Required module: %s\n' "$module"
[[ $mode == --install ]] || exit 0
[[ $EUID == 0 ]] || { echo 'Package installation requires root' >&2; exit 1; }
# Disable only vendor units introduced by this invocation. Previously installed
# services are preserved, and the main installer rejects occupied endpoints.
new_units=()
if [[ $manager == dnf ]]; then
    for specification in nginx:nginx redis:redis valkey:valkey rsync:rsyncd; do
        package=${specification%:*}
        if ! rpm -q "$package" >/dev/null 2>&1; then new_units+=("${specification#*:}.service"); fi
    done
else
    for specification in nginx:nginx redis-server:redis-server rsync:rsync; do
        package=${specification%:*}
        if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true) != 'install ok installed' ]]; then new_units+=("${specification#*:}.service"); fi
    done
fi
if [[ $manager == dnf ]]; then
    if [[ -n $module ]]; then
        if [[ -f /etc/dnf/modules.d/redis.module ]] &&
           grep -Eq '^state[[:space:]]*=[[:space:]]*enabled' /etc/dnf/modules.d/redis.module &&
           ! grep -Eq '^stream[[:space:]]*=[[:space:]]*6[[:space:]]*$' /etc/dnf/modules.d/redis.module; then
            echo 'A different Redis module stream is enabled. Prepare Redis 6 explicitly; no automatic reset/switch is performed.' >&2
            exit 1
        fi
        dnf -y module enable "$module"
    fi
    dnf -y install "${packages[@]}"
else
    # Prevent Debian maintainer scripts from starting default public services
    # before OCRUN's addresses and access rules have been installed.
    policy_created=0
    if [[ -e /usr/sbin/policy-rc.d || -L /usr/sbin/policy-rc.d ]]; then
        echo 'Existing policy-rc.d is retained. Existing service policies must deny automatic starts during fresh deployment.'
    else
        (set -o noclobber; printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d)
        chmod 0755 /usr/sbin/policy-rc.d
        policy_created=1
    fi
    cleanup_policy() { if (( policy_created )); then rm -f -- /usr/sbin/policy-rc.d; fi; }
    trap cleanup_policy EXIT
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
fi
if command -v systemctl >/dev/null; then
    for unit in "${new_units[@]}"; do
        if [[ -f /usr/lib/systemd/system/$unit || -f /lib/systemd/system/$unit ]]; then systemctl --root=/ disable "$unit"; fi
    done
fi
