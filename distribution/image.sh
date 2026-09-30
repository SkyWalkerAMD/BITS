#!/bin/bash
set -euo pipefail
shopt -s globstar nullglob
if command -v dnf >/dev/null; then
    packages=(/root/native-packages/**/*.rpm)
    [[ ${#packages[@]} == 2 ]]
    dnf -y install "${packages[@]}"
else
    packages=(/root/native-packages/**/*.deb)
    [[ ${#packages[@]} == 2 ]]
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
fi
