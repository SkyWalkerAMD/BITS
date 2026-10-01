#!/bin/bash
# Disposable Linux Actions fixture only, never packaged.
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true ]]
source /etc/os-release
if [[ $ID == rocky ]]; then
    mkdir -p /root/ci-original-repositories
    for repo in /etc/yum.repos.d/*.repo; do mv "$repo" /root/ci-original-repositories/; done
    keys=''
    for key in /etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky*; do [[ ! -f $key ]] || keys+="file://$key "; done
    [[ -n $keys ]]
    for component in BaseOS AppStream; do
        printf '[ci-%s]\nname=Official Rocky %s\nbaseurl=https://dl.rockylinux.org/pub/rocky/%s/%s/x86_64/os/\nenabled=1\ngpgcheck=1\ngpgkey=%s\ntimeout=20\nretries=2\n' "$component" "$component" "${VERSION_ID%%.*}" "$component" "$keys" >> /etc/yum.repos.d/ci.repo
    done
fi
if command -v dnf >/dev/null; then
    dnf install -y systemd dbus procps-ng iproute python3
    dnf install -y /packages/*.rpm
else
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends systemd systemd-sysv dbus procps iproute2 python3 /packages/*.deb
fi
systemctl mask systemd-udevd.service systemd-udev-trigger.service getty.target console-getty.service
