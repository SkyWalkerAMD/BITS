"""Detect original updater/manual replacement of managed hooks."""
from common import digest, json_read, read
from bits_layout import LAYOUT


def verify(app):
    marker = json_read(app / LAYOUT.install_marker)
    if marker.get('detached'):
        raise ValueError('Finalization hook is detached')
    if not marker.get('managed_files'):
        raise ValueError('Missing managed file inventory')
    for name, expected in marker['managed_files'].items():
        if name.startswith('/') or '..' in name.split('/'):
            raise ValueError('Invalid managed file inventory')
        if digest(read(app / name)) != expected:
            raise ValueError('Managed file changed: ' + name)
