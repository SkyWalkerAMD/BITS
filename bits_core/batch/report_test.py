"""Cloud-only report behavior tests, alongside real finalization integration."""
import hashlib
import json
import os
from pathlib import Path
import tracemalloc
import unittest
from unittest import mock


def make_checks(base):
    class ReportChecks(base):
        # Inherit fixture helpers, not the existing base test cases.
        def test_report_failure_and_partial_write_retry_preserve_sources(self):
            case, mon = self.start(tag='CLOUD-DEMO')
            state = self.state(case)
            sheet = self.module.report_sheet
            original = sheet.render
            with mock.patch.object(sheet, 'render', side_effect=ValueError('injected renderer failure')):
                with self.assertRaises(ValueError):
                    self.module.finish(self.app, state)
            self.assertEqual('acceptance_report', self.state(case)['stage'])
            self.assertFalse(mon.with_suffix('.finish.json').exists())
            files = self.module.paths(self.app, state)[:3]
            frozen = [p.read_bytes() for p in files]
            # Simulate power loss after JSON publication but before HTML.
            from common import atomic as real_atomic
            def interrupted(path, *args, **kwargs):
                if str(path).endswith('.report.html'):
                    raise OSError('injected interruption between files')
                return real_atomic(path, *args, **kwargs)
            with mock.patch('common.atomic', side_effect=interrupted):
                with self.assertRaises(OSError):
                    self.module.finish(self.app, self.state(case))
            partial = mon.with_suffix('.report.json').read_bytes()
            self.cli('retry', '--case', case)
            self.assertEqual(partial, mon.with_suffix('.report.json').read_bytes())
            self.assertEqual(frozen, [p.read_bytes() for p in files])
            self.assertEqual('complete', self.state(case)['stage'])
            record = json.loads(partial)
            self.assertEqual('not_automatically_assessed', record['hardware_acceptance'])
            self.assertIsNone(record['statistics']['metrics']['vrm_temp_c']['mean'])
            self.assertEqual(4, record['statistics']['metrics']['vrm_temp_c']['missing'])
            self.assertEqual('batch_start', record['machine']['capture_phase'])
            # Visual fixture is explicitly marked synthetic, never a hardware test.
            record['task_id'] = 'CLOUD-DEMO / 模拟数据，非硬件验收'
            record['machine']['dmi']['product_name'] = 'Cloud fixture'
            preview = Path('/results/report-preview.html')
            preview.write_bytes(original(record))
            preview.chmod(0o644)
            Path('/results/report-preview.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
            os.chmod('/results/report-preview.json', 0o644)

        def test_detailed_report_tamper_or_link_never_overwritten(self):
            case, mon = self.start()
            target = mon.with_suffix('.report.html')
            target.write_text('keep this evidence')
            self.assertNotEqual(0, self.cli('finish', '--case', case, good=False).returncode)
            self.assertEqual('keep this evidence', target.read_text())
            self.assertFalse(mon.with_suffix('.finish.json').exists())
            target.unlink()
            target.symlink_to(mon)
            before = mon.read_bytes()
            self.assertNotEqual(0, self.cli('retry', '--case', case, good=False).returncode)
            self.assertEqual(before, mon.read_bytes())

        def test_v1_completed_record_keeps_original_three_files(self):
            case, mon = self.start()
            state = self.state(case)
            state.pop('detailed_report_schema')
            state.pop('machine_snapshot')
            self.module.persist(self.app, state)
            self.cli('finish', '--case', case)
            self.assertEqual(3, len(self.state(case)['artifacts']))
            before = mon.with_suffix('.finish.json').read_bytes()
            self.cli('retry', '--case', case)
            self.assertEqual(before, mon.with_suffix('.finish.json').read_bytes())
            self.assertFalse(mon.with_suffix('.report.html').exists())

        def test_escaping_zero_missing_and_long_bounded_statistics(self):
            sheet = self.module.report_sheet
            stats = sheet.Statistics()
            row = ['sckocp-v1', '20260928_010000', 'Stress', '1', '1', '4000', '0', '', '100', '', '']
            detail = {'provider': {'status': 'ok', 'data': {'schema': 'sckocp-mon-v1', 'version': '1.2.0', 'cores': [], 'sockets': []}}}
            stats.consume(row, detail)
            row[6] = ''
            tracemalloc.start()
            for i in range(260001):
                row[3] = str(i * 2 + 3)
                stats.consume(row, detail)
            unused, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertLess(peak, 8 * 1024 ** 2)
            self.assertLessEqual(len(stats.trend), 240)
            value = stats.value()
            self.assertEqual(1, value['metrics']['cpu_temp_c']['zero_count'])
            self.assertEqual(260001, value['metrics']['cpu_temp_c']['missing'])
            self.assertEqual(260002, sum(b['rows'] for b in value['trend']))
            state = {'case': 'a' * 32, 'task_id': '<script>alert(1)</script>', 'task_time': 'T',
                     'sealed_at': 'T', 'created_at': 'T', 'version': 'test', 'artifacts': {},
                     'execution_result': 'early_exit', 'steps': [{'name': '<img src=x onerror=x>', 'exit_code': 1}]}
            record = sheet.make_record(state, stats)
            rendered = sheet.render(record).decode('utf-8')
            self.assertNotIn('<script>', rendered)
            self.assertNotIn('<img', rendered)
            self.assertIn('&lt;script&gt;', rendered)
            self.assertIn('Content-Security-Policy', rendered)
            self.assertEqual('not_recorded_at_batch_start', record['machine']['capture_phase'])
            self.assertTrue(record['issues'])
    for name in dir(base):
        if name.startswith('test_') and name not in ReportChecks.__dict__:
            setattr(ReportChecks, name, None)
    return ReportChecks
