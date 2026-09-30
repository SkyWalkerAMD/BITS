#!/bin/bash
# CI dependency preparation; does not change target-machine repositories.
set -euo pipefail
mode=${1:?build or runtime}
. /etc/os-release
if command -v dnf >/dev/null; then
    extra=()
    if [ "$mode" = build ]; then extra=(gcc gcc-c++ make autoconf automake rpm-build numactl-devel); fi
    dnf -y --setopt=install_weak_deps=False install python3 tar gzip ca-certificates perl make which findutils util-linux gmp numactl-libs libatomic procps-ng "${extra[@]}"
else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    extra=()
    if [ "$mode" = build ]; then extra=(build-essential autoconf automake dpkg-dev libnuma-dev); fi
    apt-get install -y --no-install-recommends python3 tar gzip ca-certificates perl make gcc libc6-dev libgmp10 libnuma1 libatomic1 procps util-linux "${extra[@]}"
fi
