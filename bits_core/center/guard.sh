#!/bin/bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
case "${1:-}" in
    start)
        # An existing table is only ours after its contents match our saved
        # running copy. Refuse another owner instead of flushing any ruleset.
        if nft list table inet bits_center >/dev/null 2>&1; then
            [[ -f /run/bits-center-guard.sha256 ]] || { echo 'Existing unowned bits_center nft table' >&2; exit 1; }
            nft -s list table inet bits_center | sha256sum -c /run/bits-center-guard.sha256
        else
            nft -f /etc/bits/center/ingress.nft
        fi
        nft -s list table inet bits_center | sha256sum > /run/bits-center-guard.sha256
        ;;
    stop)
        if [[ -f /run/bits-center-guard.sha256 ]] && nft list table inet bits_center >/dev/null 2>&1; then
            nft -s list table inet bits_center | sha256sum -c /run/bits-center-guard.sha256
            nft delete table inet bits_center
            rm -f -- /run/bits-center-guard.sha256
        fi
        ;;
    *) echo 'Usage: bits-center-guard start|stop' >&2; exit 2 ;;
esac
