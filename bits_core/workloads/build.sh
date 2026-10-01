#!/bin/bash
set -euo pipefail
[ "${GITHUB_ACTIONS:-}" = true ] && [ "$(uname -s)" = Linux ]
export LC_ALL=C SOURCE_DATE_EPOCH=1790467200
src=/src
work=/build/workloads
mkdir -p "$work" /out
python3 "$src/bits_core/workloads/package.py" extract /inputs "$work"
flags='-O2 -march=x86-64 -mtune=generic -fstack-protector-strong -D_FORTIFY_SOURCE=2'
(cd "$work/stress" && ./configure --prefix=/opt/ocrun-workloads/0.1.0 CFLAGS="$flags" && make -j2)
(cd "$work/stress-ng" && make -j2 CFLAGS="$flags" PRESERVE_CFLAGS=1)
(cd "$work/mbw" && make CFLAGS="$flags")
(cd "$work/cyclictest" && make -j2 cyclictest no_libcpupower=1 CFLAGS="$flags")
(cd "$work/unixbench/UnixBench" && make -j2 UB_GCC_OPTIONS='-O2 -ffast-math -march=x86-64 -mtune=generic')
python3 "$src/bits_core/workloads/package.py" package "$work" /out
