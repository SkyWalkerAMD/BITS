"""Filename adapter for pre-0.3 OCRUN finalizers; no collector implementation.

Installed only in the legacy overlay as mon_sensors_plugin/runtime.py. Earlier
finalizers verify the helper inventory and load this exact filename directly.
The native BITS node does not include this adapter.
"""
import importlib.util
from pathlib import Path

_target = Path(__file__).absolute().parents[1] / 'bits_core/collector/runtime.py'
_spec = importlib.util.spec_from_file_location('_bits_legacy_runtime', str(_target))
_runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_runtime)

# Preserve the existing finalizer's supervision API. Functions retain the
# canonical module globals, including its real filename when supervising.
LOCK_NAME = _runtime.LOCK_NAME
_open_lock = _runtime._open_lock
_verified_owner = _runtime._verified_owner
_inode = _runtime._inode

if __name__ == '__main__':
    raise SystemExit(_runtime.main())
