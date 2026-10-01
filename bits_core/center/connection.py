"""Private connection file for nodes joining this new authenticated center."""
import ipaddress
import os
from pathlib import Path
import re

from . import safe, tasks


def validate(value):
    expected = {'schema', 'node', 'rdb_server', 'rdb_port', 'log_server', 'password', 'protocol'}
    if not isinstance(value, dict) or set(value) != expected or value['schema'] != 'ocrun-node-connection-v1' or value['protocol'] != 'ocrun-legacy-v1':
        raise ValueError('Unknown node connection format')
    tasks.name(value['node'])
    for key in ('rdb_server', 'log_server'):
        if not isinstance(value[key], str):
            raise ValueError('Server addresses must be IPv4 strings')
        address = ipaddress.IPv4Address(value[key])
        if address.is_unspecified or address.is_multicast:
            raise ValueError('An explicit server address is required')
    if type(value['rdb_port']) is not int or value['rdb_port'] != 6379 or not isinstance(value['password'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['password']):
        raise ValueError('Invalid database credentials or port')
    return value


def export(config, node, output):
    if os.geteuid() != 0:
        raise ValueError('Export connection credentials as root')
    output = Path(output).absolute()
    if output == Path(config['public']) or Path(config['public']) in output.parents or output == Path(config['results']) or Path(config['results']) in output.parents:
        raise ValueError('Connection credentials must not be published in resource/result directories')
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; use a new private filename')
    value = validate({'schema': 'ocrun-node-connection-v1', 'node': node,
                      'rdb_server': config['address'], 'rdb_port': 6379,
                      'log_server': config['address'], 'password': config['node_password'],
                      'protocol': config['protocol']})
    safe.save(output, value)
    return {'status': 'exported', 'path': str(output), 'mode': '0600', 'node': node,
            'password_printed': False, 'automatic_task_polling': False}
