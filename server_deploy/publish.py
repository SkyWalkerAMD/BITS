"""Publish an explicitly selected installer by expected content hash."""
import os
from pathlib import Path
import re

from . import safe

INSTALLER_NAME = re.compile(
    r'(?:(?:sckocp-api|mon-sensors-plugin|mon-sensors-finish|mon-sensors-report-py36|ocrun-node-update)-[0-9][A-Za-z0-9_.-]*\.(?:run|tar\.gz)'
    r'|ocrun-(?:workloads|node)-[0-9][A-Za-z0-9_.-]*\.x86_64\.rpm'
    r'|ocrun-(?:workloads|node)_[0-9][A-Za-z0-9_.+~-]*_amd64\.deb)\Z')


def publish(config, filename, checksum):
    if os.geteuid() != 0:
        raise ValueError('Publishing installation materials requires root')
    path = Path(filename).absolute()
    if not INSTALLER_NAME.fullmatch(path.name):
        raise ValueError('Only explicitly named node component installers may be published; credentials/configuration are excluded')
    if not re.fullmatch(r'[0-9a-f]{64}', checksum):
        raise ValueError('An independently supplied SHA-256 is required')
    data = safe.read(path, 128 * 1024 * 1024)
    if safe.digest(data) != checksum:
        raise ValueError('Installer checksum mismatch; nothing was published')
    directory = Path(config['public']) / 'releases' / checksum
    safe.mkdir(directory)
    target = directory / path.name
    if target.exists() or target.is_symlink():
        if safe.digest(safe.read(target, 128 * 1024 * 1024)) != checksum:
            raise ValueError('Published content changed; retained for inspection')
    else:
        safe.write(target, data, 0o644)
        if config['platform']['family'] == 'el':
            from .install import command
            command(['restorecon', '-RF', str(directory)])
    return {'status': 'published', 'sha256': checksum, 'bytes': len(data),
            'url': 'http://{}/releases/{}/{}'.format(config['address'], checksum, path.name),
            'nodes_updated': False}
