import threading
import unittest

import fakeredis

from ocrun.rediswire import Redis, RedisError
from ocrun.queue import Queue


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = fakeredis.TcpFakeServer(("127.0.0.1", 0), server_version=(6, 0))
        setup = fakeredis.FakeRedis(server=cls.server.fake_server, decode_responses=True)
        setup.execute_command("ACL", "SETUSER", "admin", "on", ">local-test-secret", "~*", "+@all")
        setup.execute_command("ACL", "SETUSER", "node", "on", ">node-test-secret", "~ocrun:h:wire-node:*", "+@all")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.settings = {"host": "127.0.0.1", "port": cls.server.server_address[1], "username": "admin", "password": "local-test-secret"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def test_authenticated_tcp_queue_lifecycle(self):
        queue = Queue(Redis(self.settings), "wire-node")
        self.assertTrue(queue.enqueue({"id": "wire-job", "tool": "stress", "duration_seconds": 5}))
        self.assertTrue(queue.acquire_session("wire-session", 180))
        task = queue.claim("wire-owner", "wire-session")
        self.assertEqual("running", task["status"])
        queue.finish(task, "completed", {"verdict": "not_evaluated"})
        self.assertEqual("completed", queue.get("wire-job")["status"])

    def test_bad_credentials_are_rejected(self):
        with self.assertRaises(RedisError):
            Redis(dict(self.settings, password="wrong")).execute("PING")

    def test_device_cannot_read_another_device_namespace(self):
        node = Redis(dict(self.settings, username="node", password="node-test-secret"))
        node.execute("SET", "ocrun:h:wire-node:example", "value")
        self.assertEqual("value", node.execute("GET", "ocrun:h:wire-node:example"))
        with self.assertRaises(RedisError):
            node.execute("GET", "ocrun:h:other-node:example")
