"""Network selection and zero-mutation boundaries; Linux CI only."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from distribution import network


def nic(name='ens192', address='192.168.50.10', prefix=24, index=2, dynamic=False):
    return {'ifindex': index, 'ifname': name, 'flags': ['BROADCAST', 'UP', 'LOWER_UP'],
            'operstate': 'UP', 'addr_info': [{'family': 'inet', 'local': address,
                                            'prefixlen': prefix, 'scope': 'global', 'dynamic': dynamic}]}


class NetworkTests(unittest.TestCase):
    def test_single_lan_scope_and_no_nic_mutations(self):
        selected = network.choose(network.interfaces([nic()]))
        self.assertEqual(('ens192', '192.168.50.10', '192.168.50.0/24'),
                         (selected['interface'], selected['address'], selected['network']))
        self.assertEqual([80, 873, 6379], selected['tcp_ports'])
        self.assertFalse(selected['nic_changes'])
        self.assertFalse(selected['tasks_started'])

    def test_two_real_nics_require_a_choice_even_when_one_has_default_route(self):
        raw = [nic(), nic('ens224', '172.20.3.241', 22, 3)]
        raw[0]['default_route'] = True
        values = network.interfaces(raw)
        with self.assertRaisesRegex(ValueError, 'Multiple network candidates'):
            network.choose(values)
        selected = network.choose(values, interface='ens224')
        self.assertEqual('172.20.0.0/22', selected['network'])
        self.assertEqual('ens224', network.choose(values, address='172.20.3.241')['interface'])

    def test_multi_address_interface_still_requires_explicit_address(self):
        entry = nic()
        entry['addr_info'].append(dict(entry['addr_info'][0], local='192.168.50.11'))
        values = network.interfaces([entry])
        with self.assertRaisesRegex(ValueError, 'Multiple network candidates'):
            network.choose(values, interface='ens192')
        self.assertEqual('192.168.50.11', network.choose(values, 'ens192', '192.168.50.11')['address'])

    def test_ignore_loopback_down_link_local_public_and_container_addresses(self):
        raw = [nic('lo', '127.0.0.1', 8, 1), nic(), nic('docker0', '172.17.0.1', 16, 3),
               nic('veth00', '172.18.0.1', 16, 4), nic('ens3', '169.254.1.2', 16, 5),
               nic('ens4', '8.8.8.8', 24, 6), nic('ens5', '10.2.3.4', 24, 7),
               nic('wg0', '10.9.0.1', 24, 8)]
        raw[6]['operstate'] = 'DOWN'
        values = network.interfaces(raw)
        self.assertEqual('ens192', network.choose(values)['interface'])
        self.assertEqual(7, sum(not row['eligible'] for row in values))
        for name in ('docker0', 'ens5', 'nonexistent'):
            with self.assertRaisesRegex(ValueError, 'No eligible'):
                network.choose(values, interface=name)

    def test_no_valid_address_does_not_synthesize_an_ip(self):
        with self.assertRaisesRegex(ValueError, 'does not assign'):
            network.choose(network.interfaces([]))
        with self.assertRaisesRegex(ValueError, 'No eligible'):
            network.choose(network.interfaces([nic()]), address='192.168.50.99')

    def test_dynamic_address_is_disclosed_but_not_converted_to_static(self):
        raw = [nic(dynamic=True)]
        original = copy.deepcopy(raw)
        selected = network.choose(network.interfaces(raw))
        self.assertTrue(selected['dynamic'])
        self.assertIn('DHCP reservation', selected['warnings'][0])
        self.assertEqual(original, raw)

    def test_broad_automatic_subnet_requires_explicit_access_scope(self):
        values = network.interfaces([nic(address='10.2.3.4', prefix=8)])
        with self.assertRaisesRegex(ValueError, 'too broad'):
            network.choose(values)
        self.assertEqual('10.2.3.0/24', network.choose(values, network='10.2.3.0/24')['network'])
        for scope in ('0.0.0.0/0', '10.2.4.0/24', '8.0.0.0/8', '10.2.3.4/99'):
            with self.assertRaises(ValueError):
                network.choose(values, network=scope)

    def test_suspicious_and_malformed_inventory_is_rejected(self):
        invalid = [None, {}, ['not a dictionary'], [dict(nic(), ifname='eth0;reboot')],
                   [dict(nic(), ifindex=True)], [dict(nic(), flags='UP')]]
        for prefix in (True, -1, 33, '24'):
            bad = nic()
            bad['addr_info'][0]['prefixlen'] = prefix
            invalid.append([bad])
        invalid.append([nic(), nic()])
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                network.interfaces(raw)
        with self.assertRaises(ValueError):
            network.choose(network.interfaces([nic()]), interface='ens192\nreboot')

    def test_new_nic_or_changed_address_before_apply_is_rejected(self):
        first = [nic()]
        changed = ([nic(address='192.168.50.11')], [nic(index=9)],
                   [nic(dynamic=True)], [nic(), nic('ens224', '10.5.0.1', 24, 3)])
        for after in changed:
            with self.subTest(after=after), patch.object(network, 'snapshot', side_effect=[first, after]):
                value = network.plan()
                with self.assertRaises(ValueError):
                    network.unchanged(value)

    def test_explicit_selection_survives_unrelated_interface_change(self):
        with patch.object(network, 'snapshot', side_effect=[[nic()], [nic(), nic('ens224', '10.5.0.1', 24, 3)]]):
            value = network.plan(interface='ens192')
            network.unchanged(value, interface='ens192')

    def test_only_fixed_read_only_ip_command_and_clean_environment_are_used(self):
        result = subprocess.CompletedProcess([], 0, json.dumps([nic()]).encode(), b'')
        with patch.object(network, 'system_ip', return_value='/usr/sbin/ip'), \
                patch.object(network.subprocess, 'run', return_value=result) as run:
            self.assertEqual([nic()], network.snapshot())
        self.assertEqual(['/usr/sbin/ip', '-j', '-4', 'address', 'show'], run.call_args[0][0])
        self.assertEqual({'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}, run.call_args[1]['env'])
        self.assertEqual(10, run.call_args[1]['timeout'])
        self.assertNotIn('shell', run.call_args[1])

    def test_ip_failure_invalid_json_and_large_inventory_stop_discovery(self):
        for code, output in ((1, b'[]'), (0, b'not JSON'), (0, b'x' * (1024 * 1024 + 1))):
            with patch.object(network, 'system_ip', return_value='/usr/sbin/ip'), \
                    patch.object(network.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, output, b'')):
                with self.assertRaises(ValueError):
                    network.snapshot()


if __name__ == '__main__':
    unittest.main()
