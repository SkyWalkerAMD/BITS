#!/bin/bash
# Only a disposable cloud-image fixture may adjust its own repository endpoints.
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true ]] || exit 2
source /etc/os-release
if [[ $ID == rocky ]]; then
    # Pin direct official endpoints in CI to avoid public mirror-list timeouts.
    # This is not shipped in or called by the production installer.
    mkdir -p /root/ocrun-ci-repositories
    for repo in /etc/yum.repos.d/*.repo; do [[ ! -f $repo ]] || mv "$repo" /root/ocrun-ci-repositories/; done
    keys=''
    for key in /etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky*; do [[ ! -f $key ]] || keys+="file://$key "; done
    [[ -n $keys ]]
    for component in BaseOS AppStream; do
        printf '[ci-%s]\nname=Official Rocky %s\nbaseurl=https://dl.rockylinux.org/pub/rocky/%s/%s/x86_64/os/\nenabled=1\ngpgcheck=1\ngpgkey=%s\ntimeout=20\nretries=2\n' "$component" "$component" "${VERSION_ID%%.*}" "$component" "$keys" >> /etc/yum.repos.d/ocrun-ci.repo
    done
fi
if [[ $ID == debian && $VERSION_ID == 11 ]]; then
    # Use the official security origin and revalidate repository caches.
    # Signature/expiry checks remain enabled. Never rewrite production sources.
    mkdir -p /root/ocrun-ci-debian-repositories
    for list in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        [[ -f $list ]] || continue
        cp -a "$list" /root/ocrun-ci-debian-repositories/
        sed -i 's@http://deb.debian.org/debian-security@http://security.debian.org/debian-security@g' "$list"
    done
    printf 'Acquire::http::No-Cache "true";\nAcquire::http::Max-Age "0";\n' > /etc/apt/apt.conf.d/99ocrun-ci-cache
fi
if command -v dnf >/dev/null; then
    fixture_packages=(systemd dbus procps-ng iproute)
    if ! command -v curl >/dev/null; then fixture_packages+=(curl); fi
    dnf -y install "${fixture_packages[@]}"
else
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends systemd systemd-sysv dbus procps curl iproute2
fi
bash /fixture/dependencies.sh --install
if command -v dnf >/dev/null; then dnf clean all; else apt-get clean; fi
mkdir -p /etc/systemd/system
# Unneeded device/console services in a userspace container; actual OCRUN units
# are never mocked. Keep journald and the real system manager running.
if [[ ${OCRUN_CLOUD_VM:-0} != 1 ]]; then
    systemctl mask systemd-udevd.service systemd-udev-trigger.service getty.target console-getty.service
fi
