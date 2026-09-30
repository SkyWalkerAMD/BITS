#!/bin/bash
# Upgrade only an existing compatible node; never start its scheduler or workloads.
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
node_app=/root/ocrun
node_mode=check
while (($#)); do
    case "$1" in
        --app) node_app=$2; shift 2 ;;
        --check) node_mode=check; shift ;;
        --apply) node_mode=apply; shift ;;
        --rollback) node_mode=rollback; shift ;;
        *) echo 'Usage: update-node.sh [--app /root/ocrun] --check|--apply|--rollback' >&2; exit 2 ;;
    esac
done
[[ $(id -u) == 0 && $node_app == /* ]] || exit 2
node_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd -- "$node_package"
sha256sum -c SHA256SUMS
node_report=./mon-sensors-report-py36-0.2.0.run
node_finish=./mon-sensors-finish-0.2.0.run
if [[ $node_mode == rollback ]]; then
    bash "$node_finish" --app "$node_app" --rollback --check
    bash "$node_report" --rollback --check
    bash "$node_finish" --app "$node_app" --rollback
    bash "$node_report" --rollback
    echo 'Previous managed release restored; no scheduler started.'
    exit 0
fi
bash "$node_report" --app "$node_app" --check
bash "$node_finish" --app "$node_app" --check
if [[ $node_mode == check ]]; then
    echo 'Preflight passed. Use --apply to upgrade this idle node.'
    exit 0
fi
bash "$node_report" --app "$node_app"
if ! bash "$node_finish" --app "$node_app"; then
    echo 'Finalizer update failed; restoring prior report launcher if available.' >&2
    bash "$node_report" --rollback || true
    exit 1
fi
"$node_app/mon-sensors-finish" check --scheduler
echo 'Node upgrade complete. No task was fetched or started.'
