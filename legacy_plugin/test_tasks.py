"""Real Redis fault tests, only in an isolated cloud test environment."""
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from . import tasks
from server_deploy.wire import Redis


class TasksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('GITHUB_ACTIONS') != 'true':
            raise unittest.SkipTest('Cloud Linux only')
        cls.work = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        tool = shutil.which('redis-server') or shutil.which('valkey-server')
        cls.proc = subprocess.Popen([tool, '--bind', '127.0.0.1', '--port', str(port),
            '--databases', '16', '--dir', cls.work.name, '--save', '', '--appendonly', 'no'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.redis = Redis(port=port)
        for unused in range(100):
            try:
                cls.redis.call('PING'); break
            except OSError:
                time.sleep(.05)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=10); cls.work.cleanup()

    def setUp(self):
        # This is the isolated test database, never a user-configured server.
        self.redis.call('FLUSHALL')

    def add(self, items=None, check=False):
        return tasks.submit(self.redis, 'NODE', 'BATCH', items or ['stress=2', 'stress-ng=2'], 'cloud', check)

    def test_typo_and_invalid_duration_never_write(self):
        for items in (['strss=2'], ['stress=0'], ['stress=abc'], ['stress=2678401'], ['sysjitter=2']):
            with self.assertRaises(ValueError):
                self.add(items)
            self.assertEqual(self.redis.call('DBSIZE'), 0)

    def test_dry_run_does_not_create_a_batch(self):
        self.assertEqual(self.add(check=True)['status'], 'checked')
        self.assertEqual(tasks.status(self.redis, 'NODE')['status'], 'no_batch')
        with self.assertRaises(ValueError):
            tasks.submit(self.redis, 'NODE', 'BATCH..BAD', ['stress=2'])
        self.assertEqual(self.redis.call('DBSIZE'), 0)

    def test_mapped_empty_database_is_never_reused(self):
        self.redis.call('SET', 'OTHER', 1)
        result = self.add()
        self.assertEqual(result['db'], 2)
        self.assertEqual(self.redis.call('DBSIZE', db=1), 0)

    def test_unknown_nonempty_database_and_reserved_database_preserved(self):
        self.redis.call('SET', 'third_party', 'keep', db=1)
        self.assertEqual(self.add()['db'], 2)
        self.assertEqual(self.redis.call('GET', 'third_party', db=1), 'keep')

    def test_repeated_task_names_keep_duration_and_queue_order(self):
        result = self.add(['stress=2', 'stress-ng=3', 'stress=2'])
        value = tasks.status(self.redis, 'NODE')
        self.assertEqual([r['name'] for r in value['tasks']], ['stress', 'stress-ng', 'stress'])
        self.assertEqual(self.redis.call('GET', 'stress', db=result['db']), '2')
        with self.assertRaises(ValueError):
            tasks.parse(['stress=2', 'stress=3'])

    def test_duplicate_submission_preserves_current_batch(self):
        self.add()
        before = tasks.status(self.redis, 'NODE')
        with self.assertRaises(ValueError):
            self.add()
        self.assertEqual(before, tasks.status(self.redis, 'NODE'))

    def test_cancel_unchanged_unclaimed_batch(self):
        result = self.add()
        self.assertEqual(tasks.remove(self.redis, 'NODE', 'BATCH', 'cloud')['status'], 'removed')
        self.assertEqual(self.redis.call('DBSIZE', db=result['db']), 0)
        self.assertEqual(tasks.remove(self.redis, 'NODE', 'BATCH', 'cloud')['status'], 'already_absent')

    def test_legacy_batch_without_plugin_marker_is_retained(self):
        result = self.add()
        self.redis.call('DEL', tasks.OWNER, db=result['db'])
        with self.assertRaises(ValueError):
            tasks.remove(self.redis, 'NODE', 'BATCH', 'cloud')
        self.assertEqual(self.redis.call('LLEN', 'TASKS', db=result['db']), 2)

    def test_unknown_keys_shared_mapping_changed_identity_and_claim_block_cancel(self):
        for change in ('unknown', 'shared', 'identity', 'claim', 'queue'):
            self.redis.call('FLUSHALL')
            db = self.add()['db']
            if change == 'unknown':
                self.redis.call('SET', 'unrelated', 'keep', db=db)
            elif change == 'shared':
                self.redis.call('SET', 'OTHER', db)
            elif change == 'identity':
                self.redis.call('SET', 'DATES', 'changed', db=db)
            elif change == 'claim':
                self.redis.call('SET', 'MON_PLUGIN_CLAIM', 'already_claimed', db=db)
            else:
                self.redis.call('LSET', 'TASKS', 0, 'mbw', db=db)
            before = tasks.status(self.redis, 'NODE')
            with self.assertRaises(ValueError):
                tasks.remove(self.redis, 'NODE', 'BATCH', 'cloud')
            self.assertEqual(before, tasks.status(self.redis, 'NODE'))

    def test_archive_only_delivered_empty_batch(self):
        db = self.add()['db']
        with self.assertRaises(ValueError):
            tasks.remove(self.redis, 'NODE', 'BATCH', 'cloud', archive=True)
        self.redis.call('DEL', 'TASKS', db=db)
        self.redis.call('SET', 'STATUS', 'attention_required', db=db)
        with self.assertRaises(ValueError):
            tasks.remove(self.redis, 'NODE', 'BATCH', 'cloud', archive=True)
        self.redis.call('SET', 'STATUS', 'delivered', db=db)
        self.assertEqual(tasks.remove(self.redis, 'NODE', 'BATCH', 'cloud', archive=True)['status'], 'removed')


if __name__ == '__main__':
    unittest.main()
