"""Code is root-owned; result data retains the original reader's separate policy."""
import json
import os
from pathlib import Path
import subprocess
import sys

from distribution.common import (directory, read, digest, fingerprint, write, save,
                                 sync_directory, mkdir, lock_existing)
from . import VERSION

ROOT = Path(__file__).absolute().parents[1]
CONFIG = Path('/etc/ocrun-plugin/config.json')


def verify(role=None):
    spec = json.loads(read(ROOT / 'PACKAGE.json').decode('utf-8'))
    if spec.get('version') != VERSION or spec.get('role') not in ('control', 'node'):
        raise ValueError('Unknown bits-o package')
    if role and spec['role'] != role:
        raise ValueError('This command requires the {} package'.format(role))
    for name, expected in spec['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Invalid package inventory')
        if fingerprint(ROOT / name, limit=128 * 1024 ** 2)['sha256'] != expected:
            raise ValueError('Package changed: ' + name)
    return spec


def root():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Run this node/setup operation as root on Linux')


def config():
    value = json.loads(read(CONFIG, limit=32768).decode('utf-8'))
    if value.get('schema') != 'ocrun-plugin-config-v1':
        raise ValueError('Unsupported configuration')
    verify(value['role'])
    return value


def run(argv, timeout=60, capture=True):
    # Never inherit PYTHONPATH, shell functions, LD_PRELOAD or caller-selected tools.
    try:
        result = subprocess.run([str(x) for x in argv], timeout=timeout,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                 'HOME': '/root', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
    except subprocess.TimeoutExpired:
        raise ValueError('{} exceeded its time limit; inspect status before retrying'.format(Path(argv[0]).name))
    if result.returncode:
        detail = (result.stderr or result.stdout or b'').decode('utf-8', 'replace')[-1800:]
        raise ValueError('{} failed ({}): {}'.format(Path(argv[0]).name, result.returncode, detail))
    return result.stdout


def python(script, *args):
    return [sys.executable, '-I', '-S', '-B', str(script)] + list(args)


def display(value, as_json=False):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return
    # Values may originate in the old, unauthenticated database. Escape terminal controls.
    def clean(v):
        return ''.join(c if c.isprintable() else '\\x{:02x}'.format(ord(c)) for c in str(v))
    if isinstance(value, dict):
        for key, item in value.items():
            print('{}: {}'.format(key, clean(json.dumps(item, ensure_ascii=False)
                if isinstance(item, (dict, list)) else item)))
    else:
        print(clean(value))
