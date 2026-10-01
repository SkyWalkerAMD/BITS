"""Build the standalone controller extension only in GitHub Actions Linux."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from . import VERSION

ROOT = Path(__file__).resolve().parents[2]


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux":
        raise SystemExit("Build only in the authorized GitHub Actions Linux environment")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT)).decode().strip()
    files = {}
    files['bits_core/__init__.py'] = (ROOT / 'bits_core/__init__.py').read_bytes()
    for name in ("__init__.py", "control.py", "data.py", "baseline.json"):
        files["bits_core/results/" + name] = (ROOT / "bits_core/results" / name).read_bytes()
    files["CONTROL-MANUAL.md"] = (ROOT / "docs/plugins/CONTROL-READER.md").read_bytes()
    manifest = {"version": VERSION, "source_commit": commit,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                "modifies_existing_ocrun": False, "requires_root_for_reading": False,
                "network_listener": False, "task_queue_writes": False}
    files["PACKAGE.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    contents = {"payload/" + name: data for name, data in files.items()}
    for name in ("install.py", "install.sh"):
        contents[name] = (ROOT / "bits_core/results" / name).read_bytes()
    output = ROOT / "control-dist"
    output.mkdir(exist_ok=True)
    archive = output / ("mon-sensors-control-" + VERSION + ".tar.gz")
    with tarfile.open(str(archive), "w:gz") as package:
        for name, data in sorted(contents.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o755 if name.endswith(".sh") else 0o644
            package.addfile(member, io.BytesIO(data))
    from build import self_extracting_package
    self_extracting_package(archive, output / ("mon-sensors-control-" + VERSION + ".run"), "install.sh")
    (output / "CONTROL-MANUAL.md").write_bytes(files["CONTROL-MANUAL.md"])
    (output / "PACKAGE.json").write_bytes(files["PACKAGE.json"])
    (output / "SHA256SUMS").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                                              for p in sorted(output.iterdir()) if p.name != "SHA256SUMS"))
    print(json.dumps({"version": VERSION, "source_commit": commit, "output": str(output)}))


if __name__ == "__main__":
    main()
