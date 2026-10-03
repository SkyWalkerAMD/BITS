"""Cloud-only independent BITS packages. Never run on the editing workstation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.4.3"
PREFIX = "/opt/bits/native/" + VERSION
BASELINES = {
    "rpm": ("bits-node-0.3.0-1.el8.x86_64.rpm", "28c98ed53b162c9727b54d8d68e77f72b8f89326be2fcf1ee5ba01e16439da36"),
    "deb": ("bits-node_0.3.0-1_amd64.deb", "9a700ced1422ebf7ce06f792099e74973879d0cb2313144dd23e71332936ed0f"),
}


def sha(path):
    with open(str(path), "rb") as stream:
        h = hashlib.sha256()
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
        return h.hexdigest()


def put(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    path.chmod(mode)


def copy(source, target):
    put(target, source.read_bytes(), 0o755 if source.stat().st_mode & 0o111 else 0o644)


def inventory(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def stage_worker(stage, baseline, kind):
    expected_name, expected_hash = BASELINES[kind]
    if baseline.name != expected_name or sha(baseline) != expected_hash:
        raise ValueError("Published baseline hash differs")
    with tempfile.TemporaryDirectory(prefix="bits-selected-tools-") as tmp:
        extracted = Path(tmp)
        if kind == "deb":
            subprocess.run(["dpkg-deb", "-x", str(baseline), tmp], check=True)
        else:
            sender = subprocess.Popen(["rpm2cpio", str(baseline)], stdout=subprocess.PIPE)
            try:
                subprocess.run(["cpio", "-idm", "--quiet", "--no-absolute-filenames",
                                "./opt/bits/workloads/*", "./opt/bits/node/0.3.0/vendor/*",
                                "./opt/bits/node/0.3.0/REPORT-SOURCE.json"], cwd=tmp, stdin=sender.stdout, check=True)
            finally:
                sender.stdout.close()
            if sender.wait():
                raise ValueError("Cannot extract pinned baseline")
        tools = stage / "opt/bits/workloads/0.1.0"
        shutil.copytree(str(extracted / "opt/bits/workloads/0.1.0"), str(tools))
        worker = stage / PREFIX.lstrip("/") / "worker"
        worker.mkdir(parents=True)
        shutil.copytree(str(extracted / "opt/bits/node/0.3.0/vendor"), str(worker / "vendor"))
        copy(extracted / "opt/bits/node/0.3.0/REPORT-SOURCE.json", worker / "REPORT-SOURCE.json")
    for source, name in (
        ("native/worker/worker.py", "worker.py"),
        ("native/worker/data_api.py", "data_api.py"),
        ("bits_core/batch/workload.py", "workload.py"),
        ("bits_core/batch/common.py", "common.py"),
        ("bits_core/batch/report_sheet.py", "report_sheet.py"),
        ("bits_core/collector/collector.py", "collector.py"),
        ("bits_core/reporting/streaming.py", "streaming.py"),
        ("bits_core/workloads/suite.py", "suite.py"),
        ("sckocp_api/security.py", "security.py"),
    ):
        put(worker / name, (ROOT / source).read_bytes().replace(b"\r\n", b"\n"))
    for name in ("__init__.py", "interface.py", "provider.py", "security.py"):
        put(worker / "sckocp_api" / name, (ROOT / "sckocp_api" / name).read_bytes().replace(b"\r\n", b"\n"))
    # Shared library algorithms only. No queue, original scheduler or network
    # transport is included in the new worker.
    sys.path.insert(0, str(ROOT))
    from bits_core.packaging import write_layout
    write_layout(worker, native=True)
    put(worker / "MANIFEST.json", json.dumps(inventory(worker), sort_keys=True, indent=2))


def service(role):
    center = role == "center"
    return ("[Unit]\nDescription=BITS independent " + role +
            "\nAfter=network-online.target\nWants=network-online.target\n"
            "[Service]\nType=simple\n" +
            ("User=bits\nGroup=bits\nAmbientCapabilities=CAP_NET_BIND_SERVICE\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE\n" if center else "") +
            "ExecStart=/usr/bin/bits-" + role + (" serve" if center else " agent") +
            "\nRestart=on-failure\nRestartSec=10\nKillMode=control-group\nTimeoutStopSec=50\n"
            "UMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\n"
            "ProtectSystem=full\nProtectHome=" + ("true" if center else "false") +
            "\n[Install]\nWantedBy=multi-user.target\n")


def guard(role):
    return ("#!/bin/sh\nset -eu\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\nexport PATH\n"
            "if systemctl is-active --quiet bits-" + role + ".service; then\n"
            " echo 'Stop this BITS service before changing its package; data retained.' >&2; exit 1\nfi\n"
            "for old in /etc/bits/node/native.json /etc/bits/center/manifest.json /etc/ocrun-node/native.json /etc/ocrun-server/manifest.json; do\n"
            " if [ -e \"$old\" ]; then echo 'Earlier deployment retained; use a fresh server/node or explicit migration.' >&2; exit 1; fi\ndone\n")


def lifecycle(role, phase):
    # Embedded because new package files do not exist during preinst/%pre.
    source = (ROOT / "native/package_lifecycle.py").read_text().replace("\r\n", "\n")
    return ("#!/bin/sh\nset -eu\numask 077\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\nexport PATH\n"
            "/usr/bin/python3 -I -B - " + role + " " + VERSION + " " + phase + " <<'BITS_PACKAGE_PY'\n" +
            source + "\nBITS_PACKAGE_PY\n")


def package(role, kind, binaries, baseline, output):
    with tempfile.TemporaryDirectory(prefix="bits-native-package-") as temporary:
        work = Path(temporary)
        stage = work / "stage"
        stage.mkdir()
        copy(binaries / ("bits-" + role), stage / "usr/bin" / ("bits-" + role))
        (stage / "usr/bin" / ("bits-" + role)).chmod(0o755)
        if role == "node":
            stage_worker(stage, baseline, kind)
        put(stage / "usr/lib/systemd/system" / ("bits-" + role + ".service"), service(role))
        copy(ROOT / "docs/development/BITS-INDEPENDENT.md", stage / "usr/share/doc" / ("bits-" + role) / "ARCHITECTURE.md")
        copy(ROOT / "docs/deployment/BITS-INDEPENDENT.md", stage / "usr/share/doc" / ("bits-" + role) / "OPERATIONS.md")
        shutil.copytree(str(binaries / "licenses"), str(stage / "usr/share/doc" / ("bits-" + role) / "go-licenses"))
        put(stage / PREFIX.lstrip("/") / (role + "-BUILD.json"), json.dumps({
            "version": VERSION, "role": role, "source_commit": os.environ["GITHUB_SHA"],
            "workflow_run": os.environ["GITHUB_RUN_ID"], "native_protocol": "bits-v1",
            "legacy_protocols_installed": False, "sckocp_activation_management": False,
            "tool_baseline": {"file": baseline.name, "sha256": sha(baseline)} if role == "node" else None,
            "files": inventory(stage)}, indent=2))
        pre = lifecycle(role, "prepare")
        finish = lifecycle(role, "finish")
        recover = lifecycle(role, "recover")
        post = ("#!/bin/sh\nset -eu\n" +
                ("getent group bits >/dev/null || groupadd --system bits\ngetent passwd bits >/dev/null || useradd --system --gid bits --home-dir /var/lib/bits/center --shell /usr/sbin/nologin bits\n" if role == "center" else "") +
                "systemctl daemon-reload >/dev/null 2>&1 || true\n")
        name = "bits-" + role
        if kind == "deb":
            control = stage / "DEBIAN"
            requirements = "systemd, ca-certificates, coreutils, ipmitool"
            if role == "center":
                requirements += ", passwd"
            else:
                requirements += ", python3 (>= 3.6), libnuma1, libgmp10, libatomic1, libstdc++6, perl"
            put(control / "control", "Package: " + name + "\nVersion: " + VERSION + "-1\nArchitecture: amd64\n"
                "Maintainer: BITS project\nSection: admin\nPriority: optional\nDepends: " + requirements +
                "\nPre-Depends: python3 (>= 3.6), systemd, tar" +
                "\nConflicts: ocrun-node, ocrun-center, bits-o-node, bits-o-control, bits-o-workloads, ocrun-workloads\n"
                "Description: BITS " + role + " for test execution and hardware monitoring\n One-time setup; automatic idle service maintenance on package upgrades.\n")
            put(control / "preinst", pre, 0o755)
            # Immutable <=0.4.1 prerm refuses active services. dpkg invokes the
            # new prerm's failed-upgrade fallback before the new preinst.
            put(control / "prerm", "#!/bin/sh\nset -eu\ncase \"$1\" in\n"
                "upgrade) exit 0;;\nfailed-upgrade)\n" + pre.split("\n", 1)[1] +
                ";;\n*)\n" + guard(role).split("\n", 1)[1] + ";;\nesac\n", 0o755)
            put(control / "postinst", "#!/bin/sh\nset -eu\nif [ \"$1\" = configure ]; then\n" +
                post.split("\n", 1)[1] + finish.split("\n", 1)[1] + "\nfi\n", 0o755)
            put(control / "postrm", "#!/bin/sh\nset -eu\ncase \"$1\" in\nabort-upgrade|abort-install)\n" +
                recover.split("\n", 1)[1] + ";;\n*) systemctl daemon-reload >/dev/null 2>&1 || true;;\nesac\n", 0o755)
            put(control / "md5sums", "".join(hashlib.md5(p.read_bytes()).hexdigest() + "  " + p.relative_to(stage).as_posix() + "\n"
                for p in sorted(stage.rglob("*")) if p.is_file() and control not in p.parents))
            subprocess.run(["dpkg-deb", "-Zxz", "--uniform-compression", "--root-owner-group", "--build", str(stage),
                            str(output / (name + "_" + VERSION + "-1_amd64.deb"))], check=True)
        else:
            spec = work / "package.spec"
            requirements = "systemd, ca-certificates, coreutils, ipmitool"
            if role == "center":
                requirements += ", shadow-utils"
            else:
                requirements += ", python3 >= 3.6, numactl-libs, gmp, libatomic, libstdc++, perl"
            spec.write_text("Name: " + name + "\nVersion: " + VERSION + "\nRelease: 1.el8\n"
                "Summary: BITS " + role + " for test execution and hardware monitoring\n"
                "License: GPLv2+ and GPLv3+ and BSD and MIT and GIMPS and LicenseRef-Intel-Limited-Tools\n"
                "BuildArch: x86_64\nRequires: " + requirements +
                "\nRequires(pre,post,posttrans): python3 >= 3.6, systemd, tar\nRequires(preun): systemd" +
                "\nConflicts: ocrun-node, ocrun-center, bits-o-node, bits-o-control, bits-o-workloads, ocrun-workloads\n"
                "%global debug_package %{nil}\n%global __os_install_post %{nil}\n"
                "%description\nTest execution and hardware monitoring; automatic idle service maintenance.\n"
                "%install\nmkdir -p %{buildroot}\ncp -a " + str(stage) + "/. %{buildroot}/\n"
                "%pre\n" + pre.split("\n", 1)[1].replace("%", "%%") +
                "\n%preun\nif [ \"$1\" -eq 0 ]; then\n" + guard(role).split("\n", 1)[1] + "\nfi\n" +
                "\n%post\n" + post.split("\n", 1)[1] +
                "\n%postun\nsystemctl daemon-reload >/dev/null 2>&1 || true\n" +
                "\n%posttrans\n" + finish.split("\n", 1)[1].replace("%", "%%") +
                "\n%files\n%defattr(-,root,root,-)\n/usr/bin/bits-" + role +
                "\n/usr/lib/systemd/system/bits-" + role + ".service\n/usr/share/doc/bits-" + role +
                "\n" + PREFIX + "\n" + ("/opt/bits/workloads/0.1.0\n" if role == "node" else ""), encoding="utf-8")
            subprocess.run(["rpmbuild", "-bb", "--define", "_topdir " + str(work / "rpmbuild"), str(spec)], check=True)
            for built in (work / "rpmbuild/RPMS").rglob("*.rpm"):
                copy(built, output / built.name)


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux":
        raise SystemExit("Build only in authorized Linux Actions")
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("rpm", "deb"), required=True)
    parser.add_argument("--binaries", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for role in ("center", "node"):
        package(role, args.kind, args.binaries, args.baseline, args.output)
    put(args.output / "SHA256SUMS", "".join(sha(p) + "  " + p.name + "\n" for p in sorted(args.output.glob("*")) if p.is_file() and p.name != "SHA256SUMS"))


if __name__ == "__main__":
    main()
