# Node-only hooks. One snapshot/batch per explicit scheduler run.
run_task() {
    "${APPPATH}/mon-sensors-finish" run-queue --rdb-server "$RDBSVR" \
        --log-server "$LOGSVR" --log-dir "$LOGPATH" --node "$(hostname)" --serial "$MB_SN"
}

app_check() {
    local mf_remote mf_local
    mf_remote=$(curl -fsS --connect-timeout 5 --max-time 15 \
        "http://${LOGSVR}/config/ocrun-version.txt") || return 1
    mf_remote=$(printf '%s\n' "$mf_remote" | awk -F= '/^MAIN_VERSION=/{print $2}')
    mf_local=$(awk -F= '/^MAIN_VERSION=/{print $2}' "${APPPATH}/version.txt")
    if [[ $mf_remote != 0.9.24a || $mf_local != "$mf_remote" ]]; then
        echo 'mon-sensors-finish: resource version changed; explicit compatible upgrade required' >&2
        return 1
    fi
    "${APPPATH}/mon-sensors-finish" check --scheduler
}
