"""Build only in the authorized cloud environment; no production configuration."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.6"
NAME = "mon-sensors-finish-" + VERSION


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux":
        raise SystemExit("Build only in the authorized cloud Linux workflow")
    out = ROOT / "finish-dist"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="finish-build-") as temporary:
        stage = Path(temporary)
        payload = stage / "payload"
        payload.mkdir()
        for name in ("common.py", "finish.py", "hook.sh", "oct-hook.sh", "node.py", "queue.py",
                     "workload.py", "operator_cli.py", "install_guard.py", "tools_adoption.py", "report_sheet.py"):
            shutil.copyfile(str(ROOT / "finish_addon" / name), str(payload / name))
        shutil.copyfile(str(ROOT / "sckocp_api/security.py"), str(payload / "security.py"))
        shutil.copyfile(str(ROOT / 'workload_suite/suite.py'), str(payload / 'suite.py'))
        for name in ("install.py", "install.sh", "README.md"):
            source = ROOT / ('docs/node/OPERATIONS.md' if name == 'README.md' else 'finish_addon/' + name)
            shutil.copyfile(str(source), str(stage / name))
        ocb = (ROOT / "integrations/mon-sensors/upstream-0.9.24a/ocb").read_bytes().replace(b"\r\n", b"\n")
        ocb = ocb.replace(b'grep -e "mon_sensors"', b"grep -E 'mon[-_]sensors'")
        spec = {"version": VERSION, "compatible_ocb_sha256": [hashlib.sha256(ocb).hexdigest()], "files": {}}
        sys.path.insert(0, str(ROOT))
        from mon_sensors_plugin.install import patched
        upstream = ROOT / 'integrations/mon-sensors/upstream-0.9.24a'
        sources = {n: (upstream / n).read_bytes() for n in ('ocb', 'oct', 'mon-sensors')}
        sources['py/mon-analyse-log.py'] = (upstream / 'mon-analyse-log.py').read_bytes()
        spec['compatible_oct_sha256'] = [hashlib.sha256(patched(sources, headless=h)['oct']).hexdigest() for h in (False, True)]
        baseline = '2b1e0fe31aab59a389fc9d4d9f3cdb508b2a3c6b'
        spec['previous_files'] = {}
        for name in ('common.py', 'finish.py', 'hook.sh', 'security.py'):
            origin = 'sckocp_api/security.py' if name == 'security.py' else 'finish_addon/' + name
            data = subprocess.check_output(['git', 'show', baseline + ':' + origin], cwd=str(ROOT))
            spec['previous_files'][name] = hashlib.sha256(data).hexdigest()
        for path in sorted(payload.iterdir()):
            spec["files"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        spec['previous_releases'] = {'0.1.0': spec['previous_files'], '0.2.0': {}}
        previous = '094ff47e537aec9bfe0a2223408010530e5dc3d5'
        for name in spec['files']:
            if name in ('suite.py', 'report_sheet.py'):
                continue
            origin = 'sckocp_api/security.py' if name == 'security.py' else 'finish_addon/' + name
            data = subprocess.check_output(['git', 'show', previous + ':' + origin], cwd=str(ROOT))
            spec['previous_releases']['0.2.0'][name] = hashlib.sha256(data).hexdigest()
        spec['previous_releases']['0.2.1'] = {}
        for name in spec['files']:
            if name in ('suite.py', 'report_sheet.py'):
                continue
            origin = 'sckocp_api/security.py' if name == 'security.py' else 'finish_addon/' + name
            data = subprocess.check_output(['git', 'show', '30b56fb723091223a9308049f39b51221c24c127:' + origin], cwd=str(ROOT))
            spec['previous_releases']['0.2.1'][name] = hashlib.sha256(data).hexdigest()
        spec['previous_releases']['0.2.2'] = {}
        for name in spec['files']:
            if name in ('suite.py', 'report_sheet.py'):
                continue
            origin = 'sckocp_api/security.py' if name == 'security.py' else 'finish_addon/' + name
            data = subprocess.check_output(['git', 'show', '545860d885a5753e409ab1963fdbcb0174ae177f:' + origin], cwd=str(ROOT))
            spec['previous_releases']['0.2.2'][name] = hashlib.sha256(data).hexdigest()
        spec['previous_releases']['0.2.3'] = {}
        for name in spec['files']:
            if name == 'report_sheet.py':
                continue
            origin = ('sckocp_api/security.py' if name == 'security.py' else
                      'workload_suite/suite.py' if name == 'suite.py' else 'finish_addon/' + name)
            data = subprocess.check_output(['git', 'show', '39117329c101616ca5263b89e3865a3fbd30388e:' + origin], cwd=str(ROOT))
            spec['previous_releases']['0.2.3'][name] = hashlib.sha256(data).hexdigest()
        spec['previous_releases']['0.2.4'] = {}
        for name in spec['files']:
            if name == 'report_sheet.py':
                continue
            origin = ('sckocp_api/security.py' if name == 'security.py' else
                      'workload_suite/suite.py' if name == 'suite.py' else 'finish_addon/' + name)
            data = subprocess.check_output(['git', 'show', 'de94f341a2fd4f085ad9d85a71992bc394f5b4c6:' + origin], cwd=str(ROOT))
            spec['previous_releases']['0.2.4'][name] = hashlib.sha256(data).hexdigest()
        spec['previous_releases']['0.2.5'] = {}
        for name in spec['files']:
            origin = ('sckocp_api/security.py' if name == 'security.py' else
                      'workload_suite/suite.py' if name == 'suite.py' else 'finish_addon/' + name)
            data = subprocess.check_output(['git', 'show', 'f56965cb22eb592a934b74e13fae7637fb5c2783:' + origin], cwd=str(ROOT))
            spec['previous_releases']['0.2.5'][name] = hashlib.sha256(data).hexdigest()
        (payload / "MANIFEST.json").write_text(json.dumps(spec, sort_keys=True, indent=2) + "\n")
        archive = out / (NAME + ".tar.gz")
        with tarfile.open(str(archive), "w:gz") as bundle:
            for path in sorted(stage.rglob("*")):
                if not path.is_file():
                    continue
                data = path.read_bytes()
                info = tarfile.TarInfo(path.relative_to(stage).as_posix())
                info.size = len(data)
                info.mode = 0o755 if path.suffix == ".sh" else 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                bundle.addfile(info, io.BytesIO(data))
        module_spec = importlib.util.spec_from_file_location("project_build", str(ROOT / "build.py"))
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        module.self_extracting_package(archive, out / (NAME + ".run"), "install.sh")
        shutil.copyfile(str(stage / "README.md"), str(out / "README.md"))
        files = (NAME + ".run", NAME + ".tar.gz", "README.md")
        (out / "SHA256SUMS").write_text("".join(hashlib.sha256((out / n).read_bytes()).hexdigest() + "  " + n + "\n" for n in files))
        print(json.dumps({"version": VERSION, "installer": str(out / (NAME + ".run"))}))


if __name__ == "__main__":
    main()
