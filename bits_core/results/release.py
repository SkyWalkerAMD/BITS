"""Assemble only cloud-verified controller plugin materials."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from . import VERSION


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Release in GitHub Actions only")
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    source = root / "control-release-input"
    out = root / "control-release"
    out.mkdir(exist_ok=True)
    bundle = source / "control-package"
    for path in bundle.iterdir():
        if path.is_file() and path.name != "SHA256SUMS":
            shutil.copyfile(str(path), str(out / path.name))
    results = {}
    for label in ("alma8", "rocky8", "ubuntu22", "debian13"):
        origin = source / ("control-test-" + label)
        value = json.loads((origin / "control.json").read_text())
        if value["status"] != "passed" or value["source_commit"] != commit:
            raise ValueError("Cloud verification does not match the release: " + label)
        target = out / "validation" / label
        target.mkdir(parents=True)
        for path in origin.iterdir():
            if path.is_file():
                shutil.copyfile(str(path), str(target / path.name))
        results[label] = {"reader_tests": value["reader_tests"], "status": value["status"], "python": value["python"]}
    spec = json.loads((out / "PACKAGE.json").read_text())
    if spec["source_commit"] != commit:
        raise ValueError("Package source mismatch")
    release = {"version": VERSION, "source_commit": commit, "cloud_run": os.environ["GITHUB_RUN_ID"],
               "cloud_url": "https://github.com/" + os.environ["GITHUB_REPOSITORY"] + "/actions/runs/" + os.environ["GITHUB_RUN_ID"],
               "validation": results, "field_validated_on_215": False,
               "artifact_names": [p.name for p in out.iterdir() if p.is_file()]}
    (out / "RELEASE.json").write_text(json.dumps(release, indent=2, sort_keys=True) + "\n")
    (out / "SHA256SUMS").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.relative_to(out).as_posix() + "\n"
                                           for p in sorted(out.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"))


if __name__ == "__main__":
    main()
