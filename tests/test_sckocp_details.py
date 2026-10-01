"""Licensed text supplementation and report regression; cloud Linux only."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sckocp_api import interface, provider
from bits_core.collector import collector
from bits_core.batch import report_sheet


OVERVIEW = '''== GenuineIntel fam6  Per-socket Overview ==
  S0  Temp Max 24°C  TjMax 94°C  VCCIN 1.83 V  VID 0.9109 V
      Core 3300 MHz  Mesh 1400 MHz
      DRAM 4000 MT/s  4 DIMMs  Used 2.3/256 GB 0.9%  Mem Max 33°C
      Pkg 108.0 W  DRAM 1.8 W  PC2 0%  PC6 0%  PSU In 490.0 W  load sharing
== CPU ==
  S0  Intel(R) Xeon(R) w7-2495X  24C/24T  Base 2500 MHz
== Per-core Overview ==
  Core Freq Temp VID C0 C6 IRQ
  core0 3300 24°C 0.9109 100% 0% 1002
'''
INFO = '''== Platform ==
  Secure Boot Disabled  Lockdown none  OC Lock Disabled  x2APIC On  HT Off  NUMA 1
  IOMMU VT-d off
== CPU ==
  S0  Intel(R) Xeon(R) w7-2495X  24C/24T  fam6 model 143 stepping 8  ucode 0x2b000639
      Base 25x 2500 MHz  Max-Eff 8x  Min 5x
      Programmable: turbo-ratio yes  TDP-limit yes  TjMax-offset yes
== Turbo Ratio Limits ==
      <=2C 48x  <=4C 47x  <=6C 44x  <=10C 43x  <=14C 40x  <=18C 36x  <=20C 35x  <=24C 33x
== Thermal ==
  S0  TjMax 94°C  TCC/PROCHOT offset 0°C
== Power Limits ==
  S0  PL1 225.0 W Enabled 32.000 s  PL2 270.0 W Enabled 0.012 s
        Package: TDP 225.0 W
== Power Supplies ==
  Wall 500.0 W total
  PSU1_1 260.0 W
  PSU1_2 240.0 W
  Arrangement load sharing
== Memory ==
  DIMM           Part Number     Speed       JEDEC       VDDQ     Size     Temp
  CPU0_DIMM_A1   HMCG94AHBRA480N 4000 MT/s   6400 MT/s   1.14 V   64 GB    30°C
  CPU0_DIMM_B1   HMCG94AHBRA480N 4000 MT/s   6400 MT/s   1.14 V   64 GB    31°C
  CPU0_DIMM_E1   HMCG94AHBRA480N 4000 MT/s   6400 MT/s   1.14 V   64 GB    32°C
  CPU0_DIMM_F1   HMCG94AHBRA480N 4000 MT/s   6400 MT/s   1.14 V   64 GB    33°C
== Memory Timings ==
  S0  Primary     32-32-31-65  tCWL 30
      Refresh     tRFC 160  tREFI 3900
      Secondary   tRTP 16  tFAW 28  tRRD_S 6  tRRD_L 8  tRCD_WR 32  tRAStoCAS 96
== Cache ==
     L1d 48K  L1i 32K  L2 2048K  L3 46080K
'''


def supplement(info=INFO, overview=OVERVIEW):
    parts = {}
    for name, content in (('overview', overview), ('info', info)):
        part = provider._envelope('ok', provider.parse_console(content.encode(), name))
        part['started_at'] = part['observed_at'] = '2026-09-30T06:00:00.000Z'
        parts[name] = part
    return {'schema': provider.DETAILS_SCHEMA, 'parts': parts, 'quality': 'reported_validity_unknown'}


def base():
    payload = {'schema': 'sckocp-mon-v1', 'version': '1.2.0', 'vendor': 'GenuineIntel', 'family': 6,
               'interval_s': 1, 'sockets': [{'id': 0, 'tjmax_c': 94, 'temp_max_c': 24,
                    'vid_v': .9109, 'core_mhz': 3300, 'base_mhz': 2500, 'pkg_w': 108}],
               'cores': [{'cpu': i, 'socket': 0, 'mhz': 3300, 'temp_c': 24, 'vid_v': .9109,
                          'c0_pct': 100, 'c6_pct': 0} for i in (0, 1, 10, 11, 2, 23, 3, 9)]}
    result = provider._envelope('ok', payload)
    result['schema'] = interface.SCHEMA
    return result


def preview_record():
    stats = report_sheet.Statistics()
    context = {'time': '20260930_060000', 'task': 'Stress', 'uptime_seconds': 100, 'load1': 24}
    sample = base()
    row = collector.make_row(sample, context, native_format='v1')
    detail = {'os': context, 'provider': sample, 'details': supplement(), 'details_interval_s': 10}
    stats.consume(row, detail)
    return report_sheet.make_record({'case': '0' * 32, 'task_id': 'CLOUD-DETAILS / 模拟数据，非硬件验收',
        'task_time': '20260930', 'remote': 'fixture/CLOUD', 'sealed_at': '2026-09-30T06:01:00Z',
        'created_at': '2026-09-30T06:00:00Z', 'version': '0.2.6', 'artifacts': {}, 'steps': [],
        'execution_result': 'completed', 'data_quality': 'readings_reported_validity_unknown'}, stats)


class DetailsTests(unittest.TestCase):
    def test_native_overview_fields_and_whole_power(self):
        result = supplement()
        provider.validate_details(result)
        socket = result['parts']['overview']['data']['sockets'][0]
        for key, value in (('vccin_v', 1.83), ('dram_w', 1.8), ('memory_temp_max_c', 33), ('vid_v', .9109), ('tjmax_c', 94), ('pkg_w', 108)):
            self.assertEqual(value, socket[key])
        self.assertEqual(490, result['parts']['overview']['data']['system']['psu_input_w_reported'])
        self.assertIsNone(result['parts']['overview']['data']['system']['age_s_reported'])

    def test_primary_only_and_all_other_platform_configuration_retained(self):
        data = supplement()['parts']['info']['data']
        self.assertEqual(8, data['cpus'][0]['stepping'])
        self.assertEqual([30, 31, 32, 33], [d['temp_c'] for d in data['dimms']])
        content = '\n'.join(line for section in data['sections'] for line in section['lines'])
        for line in INFO.splitlines():
            if not line.startswith('==') and not any(group in line for group in ('Refresh', 'Secondary')):
                self.assertIn(line, content)
        for forbidden in ('tRFC', 'tREFI', 'tFAW', 'tRAStoCAS'):
            self.assertNotIn(forbidden, json.dumps(data))
        self.assertEqual([260, 240], [s['watts'] for s in data['power_supplies']])

    def test_multi_socket_shared_power_is_not_summed(self):
        text = '''== GenuineIntel fam6  Per-socket Overview ==
  S0  Temp Max 40°C  VCCIN 1.8 V
  S1  Temp Max 45°C  VCCIN 1.9 V
      S0 Pkg 100 W DRAM 2 W  S1 Pkg 110 W DRAM 3 W  PSU In 490 W
'''
        data = provider.parse_console(text.encode(), 'overview')
        self.assertEqual([100, 110], [s['pkg_w'] for s in data['sockets']])
        self.assertEqual([2, 3], [s['dram_w'] for s in data['sockets']])
        self.assertEqual(490, data['system']['psu_input_w_reported'])

    def test_partial_psu_separate_statistics_and_explicit_age(self):
        extra = supplement(INFO.replace('500.0 W total', '260.0 W total, 1 of 2 supplies reporting').replace('  PSU1_2 240.0 W', '  Readings 5 s old'))
        stat = report_sheet.DetailsStatistics()
        stat.add({'details': extra})
        self.assertEqual(260, stat.value()['system']['info_partial_psu_w']['mean'])
        self.assertIsNone(stat.value()['system']['info_psu_w']['mean'])
        self.assertEqual(5, extra['parts']['info']['data']['system']['age_s_reported'])

    def test_unavailable_sections_and_unknown_timings_not_zero(self):
        result = supplement('== Platform ==\n  HT Off\n== CPU ==\n  CPU unknown\n== Memory Timings: N/A, needs sckocp mode rw ==\n')
        provider.validate_details(result)
        self.assertEqual([], result['parts']['info']['data']['dimms'])
        rendered = report_sheet.details_configuration({'last_info': result['parts']['info'], 'first_info': result['parts']['info']})
        self.assertIn('未提供', rendered)
        self.assertIn('Primary only', rendered)

    def test_license_failure_does_not_run_info_or_leak_diagnostics(self):
        with mock.patch.object(provider, '_capture', return_value=(10, b'private diagnostic')) as capture:
            result = provider.collect_details('/usr/bin/sckocp', .1, 1)
        self.assertEqual(1, capture.call_count)
        self.assertTrue(all(p['data'] is None and p['status'] == 'license_denied' for p in result['parts'].values()))
        self.assertNotIn('private diagnostic', json.dumps(result))
        provider.validate_details(result)

    def test_fixed_operations_and_shared_deadline(self):
        def capture(binary, interval, timeout, operation):
            self.assertLessEqual(timeout, 10)
            self.assertIn(operation, ('overview', 'info'))
            return 0, (OVERVIEW if operation == 'overview' else INFO).encode()
        with mock.patch.object(provider, '_capture', side_effect=capture) as call:
            result = provider.collect_details('/usr/bin/sckocp', 1, 10)
        self.assertEqual(2, call.call_count)
        provider.validate_details(result)

    def test_mutated_or_unbounded_or_control_data_rejected(self):
        result = supplement()
        result['parts']['overview']['data']['sockets'][0]['vccin_v'] = 0
        with self.assertRaises(ValueError):
            provider.validate_details(result)
        for content in (INFO + '\x1b[2J', INFO + '== Platform ==\n', '== Platform ==\n' + 'x' * 2049):
            with self.assertRaises(ValueError):
                provider.parse_console(content.encode(), 'info')
        with self.assertRaises(provider._OutputLimit):
            provider.parse_console(b'x' * (provider.DETAILS_LIMIT + 1), 'info')

    def test_base_api_default_unchanged_and_details_gated(self):
        with mock.patch.object(provider, 'collect', return_value=base()), mock.patch.object(provider, 'collect_details', return_value=supplement()) as extra:
            self.assertNotIn('details', interface.collect())
            extra.assert_not_called()
            self.assertIn('details', interface.collect(details=True))
        with mock.patch.object(provider, 'collect', return_value=provider._envelope('license_denied')), mock.patch.object(provider, 'collect_details') as extra:
            self.assertNotIn('details', interface.collect(details=True))
            extra.assert_not_called()

    def test_collector_keeps_eleven_columns_and_persists_timed_supplement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.mon'
            with mock.patch.object(collector.sckocp_api, 'collect', return_value=base()), mock.patch.object(provider, 'collect_details', return_value=supplement()), mock.patch('sys.stderr', io.StringIO()):
                self.assertEqual(0, collector.main(['2', str(path), '--once']))
            rows = path.read_text().splitlines()
            self.assertEqual(11, len(rows[2].split(',')))
            frame = json.loads(path.with_name(path.name + '.sckocp.jsonl').read_text())
            self.assertEqual(10, frame['details_interval_s'])
            provider.validate_details(frame['details'])

    def test_report_numeric_order_after_json_round_trip_and_required_fields(self):
        record = json.loads(json.dumps(preview_record(), sort_keys=True))
        rendered = report_sheet.render(record).decode()
        table = rendered.split('CPU编号')[1].split('</table>')[0]
        positions = [table.index('<td>' + str(i) + '</td>') for i in (0, 1, 2, 3, 9, 10, 11, 23)]
        self.assertEqual(sorted(positions), positions)
        for value in ('Step 8', 'VCCIN', 'TjMax', 'PSU In', '内存 DRAM', '32-32-31-65', 'IOMMU VT-d off', 'CPU0_DIMM_F1'):
            self.assertIn(value, rendered)
        self.assertNotIn('tRAStoCAS', rendered)
        self.assertNotIn('<script', rendered)

    def test_config_changes_retained_and_html_escaped(self):
        state = report_sheet.DetailsStatistics()
        state.add({'details': supplement()})
        state.add({'details': supplement(INFO.replace('HT Off', 'HT On <script>alert(1)</script>'))})
        self.assertEqual(1, state.value()['configuration_changes_observed'])
        text = report_sheet.details_configuration(state.value())
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertIn('HT Off', text)

    def test_no_carry_forward_and_bounded_long_summary(self):
        state = report_sheet.DetailsStatistics()
        record = {'details': supplement(), 'details_interval_s': 10}
        for unused in range(10000):
            state.add(record)
            state.add({})
        result = state.value()
        self.assertEqual(10000, result['system']['overview_psu_w']['count'])
        self.assertLess(len(json.dumps(result)), 40000)

    def test_timing_filter_discards_unlocked_groups_before_output_buffer(self):
        expected = provider.parse_console(INFO.encode(), 'info')
        for size in (1, 3, 100, 65536):
            stream = provider._PrimaryInfoFilter()
            raw = INFO.encode()
            result = b''.join(stream.feed(raw[i:i + size]) for i in range(0, len(raw), size)) + stream.feed(b'', final=True)
            for forbidden in (b'tRFC', b'tREFI', b'tFAW', b'tRAStoCAS'):
                self.assertNotIn(forbidden, result)
            self.assertEqual(expected, provider.parse_console(result, 'info'))
        poisoned = supplement()
        section = next(s for s in poisoned['parts']['info']['data']['sections'] if s['name'] == 'Memory Timings')
        section['lines'].append('      Refresh tRFC 123')
        with self.assertRaises(ValueError):
            provider.validate_details(poisoned)


class PrivateCollectorTests(unittest.TestCase):
    def test_original_monitor_preserved_and_patch_repeat_safe(self):
        from bits_core.collector.install import patched, SCRIPTS
        root = Path(__file__).resolve().parents[1] / 'integrations/mon-sensors/upstream-0.9.24a'
        original = {name: (root / Path(name).name).read_bytes() for name in SCRIPTS}
        once = patched(original, headless=True)
        self.assertEqual(original['mon-sensors'].replace(b'\r\n', b'\n'), once['mon-sensors'])
        self.assertIn(b'"${APPPATH}/.bits-collector" --stop-app', once['oct'])
        self.assertEqual(once, patched(once, headless=True))


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Run in authorized cloud Linux')
    unittest.main()
