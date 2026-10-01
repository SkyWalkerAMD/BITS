"""Build-time helpers; never select the execution layout from mutable state."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def layout_bytes(native=False):
    source = (ROOT / 'bits_core/layout.py').read_bytes().replace(b'\r\n', b'\n')
    return source + (b'\nLAYOUT = NATIVE\n' if native else b'\nLAYOUT = LEGACY\n')


def write_layout(directory, native=False):
    path = Path(directory) / 'bits_layout.py'
    path.write_bytes(layout_bytes(native))
    path.chmod(0o644)
