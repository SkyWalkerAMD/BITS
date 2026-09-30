#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
source /etc/os-release
case "$ID:$VERSION_ID" in
    ubuntu:20.04|ubuntu:22.04|ubuntu:24.04|ubuntu:26.04)
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y python3 rsync ipmitool dmidecode bc numactl libgomp1 stress-ng linux-tools-common util-linux
        # Matching kernel tools may be supplied by a custom kernel repository.
        if ! apt-get install -y "linux-tools-$(uname -r)"; then
            echo 'Matching turbostat package unavailable; install tools for this kernel before acceptance testing.' >&2
        fi
        ;;
    centos:7|centos:7.9|centos:7.9.2009)
        # Repository settings belong to the operator. Never replace them with an arbitrary mirror.
        yum install -y python3 rsync ipmitool dmidecode bc numactl-libs libgomp kernel-tools util-linux
        ;;
    rocky:8|rocky:8.*|rocky:9|rocky:9.*|rocky:10|rocky:10.*)
        dnf install -y python3 rsync ipmitool dmidecode bc numactl-libs libgomp kernel-tools util-linux
        ;;
    *) echo "Unsupported client system: $ID $VERSION_ID" >&2; exit 1 ;;
esac
python3 -c 'import sys; assert sys.version_info >= (3,6), "Python 3.6+ required"'
