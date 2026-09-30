# Optional Memtest results do not mask a required upload failure.
push-log() (
    local mf_remote=${1:-${HOSTNAME}_${MB_SN}}
    local mf_files=()
    [[ $mf_remote =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ && $mf_remote != *..* ]] || return 2
    shopt -s nullglob
    mf_files=(/boot/efi/EFI/Memtest86/Benchmark/* /boot/efi/EFI/Memtest86/MemTest86*.log /boot/efi/EFI/Memtest86/MemTest86*.html)
    if ((${#mf_files[@]})); then
        mkdir -p -- "$LOGPATH/MT86" || return 1
        rsync -a -- "${mf_files[@]}" "$LOGPATH/MT86/" || return $?
    else
        echo 'OCRUN: optional Memtest86 results absent; skipped.' >&2
    fi
    mf_files=("$LOGPATH"/*)
    ((${#mf_files[@]})) || return 0
    timeout --kill-after=5s 600s rsync -a --contimeout=10 --timeout=30 -- \
        "${mf_files[@]}" "${LOGSVR}::logs/${mf_remote}/"
)
