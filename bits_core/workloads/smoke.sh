#!/bin/bash
set -euo pipefail
[ "${GITHUB_ACTIONS:-}" = true ]
if [ "${OCRUN_ALREADY_INSTALLED:-}" = true ]; then
    :
elif command -v dnf >/dev/null; then
    dnf -y --setopt=install_weak_deps=False install /src/workload-dist/*.rpm
else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends /src/workload-dist/*.deb
fi
bits-o-workloads check
taskset -pc $$
# Bound tests to two available CPUs even on runners with large shared affinity sets.
python3 -I -B /src/bits_core/workloads/test_linux.py
target=/opt/ocrun-workloads/0.1.0/mbw/mbw
cp -p "$target" /root/mbw-original
printf '\nchanged\n' >> "$target"
set +e
if command -v rpm >/dev/null; then
    rpm -U --replacepkgs /src/workload-dist/*.rpm > /results/tamper-reinstall.txt 2>&1
else
    dpkg -i /src/workload-dist/*.deb > /results/tamper-reinstall.txt 2>&1
fi
refused=$?
set -e
test "$refused" -ne 0
cmp -s "$target" /root/mbw-original && exit 1
cp -p /root/mbw-original "$target"
# A refused dpkg preinst may leave the existing version unpacked; restore only
# package-manager status now the operator-preserved bytes match the inventory.
if command -v dpkg >/dev/null; then
    if [ "$(dpkg-query -W -f='${db:Status-Status}' bits-o-workloads)" != installed ]; then
        dpkg --configure bits-o-workloads
    fi
fi
printf 'modified payload reinstall: refused, bytes preserved\n' >> /results/tamper-reinstall.txt
if command -v rpm >/dev/null; then
    rpm -V bits-o-workloads
    dnf -y remove bits-o-workloads
else
    dpkg --verify bits-o-workloads
    apt-get remove -y bits-o-workloads
fi
test ! -e /usr/bin/bits-o-workloads
printf 'uninstall: passed\n' > /results/uninstall.txt
