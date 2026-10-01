"""Immutable runtime layouts selected when a verified package is assembled.

Never choose a layout from environment variables, user data, or the existence
of a directory. The generated bits_layout module is covered by package and
installation manifests. Legacy names exist only to attach to original OCRUN.
"""
from collections import namedtuple

Layout = namedtuple('Layout', (
    'name helper state entry install_marker install_lock collector_dir '
    'collector_entry collector_program collector_marker backend runtime_lock '
    'runtime_fd scheduler workload_prefix workload_data workload_binding '
    'adopted_workloads connection report native'))

LEGACY = Layout(
    'ocrun-compat', 'mon-sensors-finish.d', '.mon-sensors-finish',
    'mon-sensors-finish', '.mon-sensors-finish-install.json',
    '.mon-sensors-install.lock', '.bits-collector.d', '.bits-collector',
    'mon-sensors-plugin', '.mon-sensors-plugin', '.mon-sensors-backend',
    '.mon-sensors-runtime.lock', 'MON_SENSORS_RUNTIME_FD', 'ocb',
    '/opt/ocrun-workloads/0.1.0', '/var/lib/ocrun-workloads',
    '.mon-sensors-workload-suite.json', '.mon-sensors-workloads',
    '/etc/ocrun-node/connection.json', '/usr/local/bin/mon-sensors-report', False)

NATIVE = Layout(
    'bits-v1', 'batch-engine', 'state', 'batch', 'installation.json',
    '.install.lock', 'collector-engine', 'collect', 'collect.py',
    'installation.json', 'collector.conf', '.collector.lock',
    'BITS_COLLECTOR_FD', 'scheduler', '/opt/bits/workloads/0.1.0',
    '/var/lib/bits/workloads', 'workloads.json', 'adopted-workloads',
    '/etc/bits/node/connection.json', '/usr/libexec/bits-report', True)

# Source-tree and standalone compatibility component default. The full-system
# builder emits a separate manifest-covered module ending in LAYOUT = NATIVE.
LAYOUT = LEGACY
