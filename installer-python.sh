#!/bin/bash
# Shared offline bootstrap. The caller supplies a fixed module and package root.
installer_python() {
    local module=$1 source=$2 candidate selected=''
    shift 2
    # Never use an interpreter or command from the caller's working directory.
    export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    unset PYTHONPATH PYTHONHOME PYTHONSTARTUP
    # Minimal EL8 nodes ship platform-python without a python3 command. Both
    # installers pin the selected interpreter in their installed launcher.
    local candidates=(python3 python3.14 python3.13 python3.12 python3.11
                      python3.10 python3.9 python3.8 python3.7 python3.6
                      /usr/libexec/platform-python)
    for candidate in "${candidates[@]}"; do
        candidate=$(command -v -- "$candidate" 2>/dev/null) || continue
        if "$candidate" -I -S -B -c 'import sys; sys.exit(0 if sys.platform.startswith("linux") and sys.version_info >= (3, 6) else 1)' >/dev/null 2>&1; then
            selected=$candidate
            break
        fi
    done
    if [[ -z $selected ]]; then
        echo 'Installation requires Linux and Python 3.6 or newer.' >&2
        echo 'Install Python 3 from your system or internal package repository, then run this installer again.' >&2
        return 1
    fi
    exec "$selected" -I -S -B -c 'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module(sys.argv.pop(1), run_name="__main__")' "$source" "$module" --source "$source" "$@"
}
