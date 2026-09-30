"""Meaningful protocol and deployment-input tests; run only in Linux CI."""
import io
import unittest

from server_deploy import connection, platforms, render, security_setup, tasks
from server_deploy.wire import ProtocolError, Redis


class ServerTests(unittest.TestCase):
    def test_selinux_runtime_equivalence_across_el_generations(self):
        self.assertEqual('/var/run', security_setup.runtime_prefix(['# EL8\n/run /var/run\n']))
        self.assertEqual('/run', security_setup.runtime_prefix(['/var/run /run\n']))
        with self.assertRaises(ValueError):
            security_setup.runtime_prefix(['/run /unexpected/location\n'])

    def test_connection_types_and_secret_format_rejected(self):
        valid = {'schema': 'ocrun-node-connection-v1', 'protocol': 'ocrun-legacy-v1',
                 'node': 'TEST-001', 'rdb_server': '192.0.2.10', 'log_server': '192.0.2.10',
                 'rdb_port': 6379, 'password': 'a' * 64}
        self.assertEqual(valid, connection.validate(valid))
        invalid = [None, [], dict(valid, rdb_server=2130706433), dict(valid, rdb_port=6379.0),
                   dict(valid, rdb_port=True), dict(valid, password='secret\nsource /tmp/evil'),
                   dict(valid, log_server='0.0.0.0'), dict(valid, extra='unrecognized')]
        for value in invalid:
            with self.assertRaises(ValueError):
                connection.validate(value)

    def test_all_explicit_system_profiles(self):
        for distro in ("rocky", "almalinux", "rhel"):
            for version in ("8.10", "9.7", "10.1"):
                value = platforms.profile({"ID": distro, "VERSION_ID": version})
                self.assertEqual("valkey" if version.startswith("10") else "redis", value["database"])
                self.assertEqual(distro == "rhel", value["subscription_required"])
        for distro, versions in (("ubuntu", ("22.04", "24.04", "26.04")), ("debian", ("11", "12", "13"))):
            for version in versions:
                self.assertEqual("apt-get", platforms.profile({"ID": distro, "VERSION_ID": version})["package_manager"])

    def test_id_like_does_not_silently_admit_untested_os(self):
        for values in ({"ID": "untrusted", "ID_LIKE": "rhel", "VERSION_ID": "9"},
                       {"ID": "ubuntu", "VERSION_ID": "25.10"},
                       {"ID": "debian", "VERSION_ID": "14"}):
            with self.assertRaises(ValueError):
                platforms.profile(values)

    def test_task_spelling_and_injection_are_rejected_before_queue_write(self):
        for item in ("strss=60", "stress;touch=60", "stress=0", "stress=-1", "stress=2678401", "stress=1.5"):
            with self.assertRaises(ValueError):
                tasks.task_list([item])

    def test_repeated_tasks_preserve_legacy_duration_semantics(self):
        self.assertEqual(2, len(tasks.task_list(["stress=60", "stress=60"])))
        with self.assertRaises(ValueError):
            tasks.task_list(["stress=60", "stress=120"])

    def test_selected_workload_catalog_and_spec_legacy_alias(self):
        for name in ('stress', 'stress-ng', 'stress_r2', 'stress-ng_r2', 'mlc', 'mbw',
                     'cyclictest', 'unixbench', 'cpu', 'cpu2017'):
            self.assertEqual(name, tasks.task_list([name + '=60'])[0]['name'])
        for isa in ('no', 'avx', 'fma3', 'avx512'):
            for mode in (1, 2, 4):
                self.assertEqual(1, len(tasks.task_list(['p95-{}_m{}=60'.format(isa, mode)])))
        for name in ('sysjitter', 'ptu', 'bcfi', 'bcfd', 'bcr', 'p95-no_m3', 'mbw_r2'):
            with self.assertRaises(ValueError):
                tasks.task_list([name + '=60'])

    def test_native_tool_package_publication_names_exclude_private_files(self):
        from server_deploy.publish import INSTALLER_NAME
        for name in ('ocrun-workloads-0.1.0-1.el8.x86_64.rpm',
                     'ocrun-workloads_0.1.0-1_amd64.deb', 'mon-sensors-finish-0.2.3.run',
                     'ocrun-workloads-0.1.0-2.el8.x86_64.rpm',
                     'ocrun-workloads_0.1.0-2_amd64.deb', 'mon-sensors-finish-0.2.4.run',
                     'bits-node-0.2.4-1.el8.x86_64.rpm', 'bits-node_0.2.4-1_amd64.deb',
                     'bits-o-node-0.1.2-1.el8.x86_64.rpm', 'bits-o-workloads_0.1.0-3_amd64.deb'):
            self.assertIsNotNone(INSTALLER_NAME.fullmatch(name))
        for name in ('connection.json', 'server.json', 'other.rpm', '../ocrun-workloads_1_amd64.deb',
                     'ocrun-workloads_0.1.0-1_arm64.deb', 'sckocp-activation.key'):
            self.assertIsNone(INSTALLER_NAME.fullmatch(name))

    def test_unknown_or_unsafe_batch_identity(self):
        for value in ("../root", "test\nSET x y", "", "-option", "x" * 129):
            with self.assertRaises(ValueError):
                tasks.name(value)

    def test_render_keeps_original_endpoints_and_mandatory_access_guard(self):
        for distro, version in (("rocky", "8.10"), ("almalinux", "10.0"), ("debian", "13")):
            config = render.configuration("192.168.50.10", "192.168.50.0/24", platforms.profile({"ID": distro, "VERSION_ID": version}), "a" * 64)
            files = render.files(config, "/usr/bin/python3")
            self.assertIn("port 6379", files["/etc/ocrun-server/database.conf"][0])
            self.assertIn("protected-mode yes", files["/etc/ocrun-server/database.conf"][0])
            self.assertNotIn("nopass", files["/etc/ocrun-server/database.conf"][0])
            self.assertIn("[logs]", files["/etc/ocrun-server/rsyncd.conf"][0])
            self.assertIn("[ocrun]", files["/etc/ocrun-server/rsyncd.conf"][0])
            self.assertNotIn("flush ruleset", files["/etc/ocrun-server/ingress.nft"][0])
            self.assertIn("Requires=ocrun-server-firewall.service", files["/etc/systemd/system/ocrun-server-db.service"][0])
            self.assertFalse(config["automatic_task_polling"])

    def test_unbounded_network_and_wildcard_address_rejected(self):
        profile = platforms.profile({"ID": "debian", "VERSION_ID": "12"})
        for address, network in (("0.0.0.0", "10.0.0.0/8"), ("10.0.0.1", "0.0.0.0/0"), ("10.0.0.1\nport 22", "10.0.0.0/8")):
            with self.assertRaises(ValueError):
                render.configuration(address, network, profile)

    def test_bounded_resp_decoder(self):
        self.assertEqual(["OK", 3, None], Redis.decode(io.BytesIO(b"*3\r\n+OK\r\n:3\r\n$-1\r\n")))
        for value in (b"$8388609\r\n", b"*20001\r\n", b"$5\r\nabc\r\n", b"*1\r\n" * 20 + b"+OK\r\n"):
            with self.assertRaises(ProtocolError):
                Redis.decode(io.BytesIO(value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
