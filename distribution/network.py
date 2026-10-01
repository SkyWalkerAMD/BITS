"""Read existing IPv4 addresses and prepare BITS service scope; never edit NICs."""
import ipaddress
import json
from pathlib import Path
import re
import stat
import subprocess


PRIVATE = tuple(ipaddress.IPv4Network(value) for value in
                ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
NAME = re.compile(r'[A-Za-z0-9_.:@-]{1,64}\Z')
VIRTUAL = re.compile(r'^(?:docker[0-9]+|virbr[0-9]+|br-|veth|tun[0-9]+|tap[0-9]+|wg[0-9]+)')


def system_ip():
    for name in ('/usr/sbin/ip', '/usr/bin/ip', '/sbin/ip', '/bin/ip'):
        path = Path(name)
        if not path.exists():
            continue
        path = path.resolve(strict=True)
        for directory in path.parents:
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Untrusted system ip directory: ' + str(directory))
        info = path.lstat()
        # Distribution-owned executables may have hard links; configuration/data
        # trust rules elsewhere are not relaxed by this executable-only check.
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                info.st_mode & 0o7022 or not info.st_mode & 0o111):
            raise ValueError('Untrusted system ip program: ' + str(path))
        return str(path)
    raise ValueError('Install the distribution iproute/iproute2 package before network discovery')


def snapshot():
    result = subprocess.run([system_ip(), '-j', '-4', 'address', 'show'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ValueError('Cannot read bounded IPv4 interface information using ip -j')
    try:
        return json.loads(result.stdout.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('System ip did not return valid JSON; automatic setup stopped')


def interfaces(raw):
    if not isinstance(raw, list) or len(raw) > 4096:
        raise ValueError('Unexpected interface inventory')
    output, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('Unexpected interface entry')
        name, index = item.get('ifname'), item.get('ifindex')
        flags, addresses = item.get('flags', []), item.get('addr_info', [])
        if (not isinstance(name, str) or not NAME.fullmatch(name) or
                type(index) is not int or index < 1 or not isinstance(flags, list) or
                not all(isinstance(flag, str) for flag in flags) or
                not isinstance(addresses, list) or len(addresses) > 512):
            raise ValueError('Invalid interface name, index or address list')
        for value in addresses:
            if not isinstance(value, dict):
                raise ValueError('Invalid address entry')
            if value.get('family') != 'inet':
                continue
            address, prefix = value.get('local'), value.get('prefixlen')
            if not isinstance(address, str) or type(prefix) is not int or not 0 <= prefix <= 32:
                raise ValueError('Invalid IPv4 address or prefix')
            ip = ipaddress.IPv4Address(address)
            network = ipaddress.IPv4Network('{}/{}'.format(ip, prefix), strict=False)
            if (index, str(ip)) in seen:
                raise ValueError('Duplicate interface address; inspect network configuration')
            seen.add((index, str(ip)))
            reason = None
            if 'LOOPBACK' in flags or ip.is_loopback:
                reason = 'loopback'
            elif 'UP' not in flags or item.get('operstate') not in ('UP', 'UNKNOWN'):
                reason = 'interface_not_up'
            elif VIRTUAL.match(name):
                reason = 'container_or_tunnel_interface'
            elif not any(ip in scope for scope in PRIVATE):
                reason = 'not_rfc1918_private_ipv4'
            elif value.get('scope') != 'global' or value.get('valid_life_time') == 0:
                reason = 'address_not_usable'
            elif any(value.get(key) for key in ('tentative', 'dadfailed', 'deprecated')):
                reason = 'address_not_usable'
            output.append({'interface': name, 'ifindex': index, 'address': str(ip),
                           'prefixlen': prefix, 'network': str(network),
                           'dynamic': value.get('dynamic') is True,
                           'eligible': reason is None, 'excluded_reason': reason})
    return sorted(output, key=lambda entry: (entry['ifindex'], int(ipaddress.IPv4Address(entry['address']))))


def inventory():
    return {'schema': 'bits-network-v1', 'read_only': True, 'interfaces': interfaces(snapshot()),
            'nic_changes': False, 'tasks_started': False}


def choose(entries, interface=None, address=None, network=None):
    if interface is not None and (not isinstance(interface, str) or not NAME.fullmatch(interface)):
        raise ValueError('Invalid --interface name')
    if address is not None:
        address = str(ipaddress.IPv4Address(address))
    candidates = [entry for entry in entries if entry['eligible'] and
                  (interface is None or entry['interface'] == interface) and
                  (address is None or entry['address'] == address)]
    if not candidates:
        raise ValueError('No eligible existing private IPv4 address. Use bits-center network to inspect; '
                         'automatic setup does not assign an address')
    if len(candidates) != 1:
        choices = ', '.join('{}={}/{}'.format(v['interface'], v['address'], v['prefixlen']) for v in candidates)
        raise ValueError('Multiple network candidates: ' + choices +
                         '. Select --interface NAME; for multiple addresses also specify --address IP')
    selected = dict(candidates[0])
    scope = ipaddress.IPv4Network(network or selected['network'], strict=False)
    ip = ipaddress.IPv4Address(selected['address'])
    if scope.prefixlen < 8 or not any(scope.network_address in p and scope.broadcast_address in p for p in PRIVATE):
        raise ValueError('Automatic setup requires a bounded private IPv4 access network')
    if network is None and scope.prefixlen < 16:
        raise ValueError('Detected subnet is too broad for automatic access rules: {}. '
                         'Specify an approved --network explicitly'.format(scope))
    if ip not in scope:
        raise ValueError('Automatic setup requires the selected server address inside --network; '
                         'review routed or multiple-subnet deployments explicitly')
    warnings = []
    if selected['dynamic']:
        warnings.append('Address is dynamic: retain it with a DHCP reservation or a separately configured static IP')
    selected.update({'schema': 'bits-network-plan-v1', 'network': str(scope),
                     'detected_network': selected['network'], 'tcp_ports': [80, 873, 6379],
                     'nic_changes': False, 'tasks_started': False, 'warnings': warnings})
    return selected


def plan(interface=None, address=None, network=None):
    return choose(interfaces(snapshot()), interface, address, network)


def unchanged(previous, interface=None, address=None, network=None):
    # Re-evaluate ambiguity too: a second NIC added after discovery must not
    # silently disappear just because the first result contained one address.
    current = plan(interface, address, network)
    keys = ('interface', 'ifindex', 'address', 'prefixlen', 'network', 'dynamic')
    if any(current[key] != previous[key] for key in keys):
        raise ValueError('Network changed during discovery; rerun the check before applying')


def display(value):
    if value.get('schema') == 'bits-network-v1':
        lines = ['现有 IPv4 地址（只读检测）：']
        for entry in value['interfaces']:
            state = '可选' if entry['eligible'] else '排除: ' + entry['excluded_reason']
            lines.append('  {}  {}/{}  {}'.format(entry['interface'], entry['address'], entry['prefixlen'], state))
        return '\n'.join(lines)
    lines = ['BITS 网络配置计划：',
             '  网卡={}  服务地址={}  允许来源={}'.format(value['interface'], value['address'], value['network']),
             '  TCP 端口=80,873,6379；沿用现有网卡 IP、网关和 DNS。']
    lines.extend('  提示：' + warning for warning in value['warnings'])
    return '\n'.join(lines)
