"""Assemble a preview only from the same successful private Linux matrix."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

assert sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution.source_export import export_sources

version = "0.4.0-alpha.1"
run = os.environ["GITHUB_RUN_ID"]
commit = os.environ["GITHUB_SHA"]
inputs = Path("native-evidence")
out = Path("native-release")
out.mkdir()
reports = {}
for label in ("rocky8", "rocky9", "rocky10", "alma8", "alma9", "alma10",
              "debian11", "debian12", "debian13", "ubuntu22", "ubuntu24", "ubuntu26"):
    evidence = inputs / ("independent-test-" + label + "-" + run)
    summary = json.loads((evidence / "acceptance.json").read_text())
    if summary["status"] != "passed" or summary["version"] != version:
        raise ValueError("Incomplete distribution acceptance: " + label)
    summary["image"] = (evidence / "image.txt").read_text().strip()
    dependency_receipt = evidence / "debian11-dependency-receipt.json"
    if dependency_receipt.exists():
        summary["signed_index_dependency_receipt"] = json.loads(dependency_receipt.read_text())
    if label in ("rocky8", "ubuntu22"):
        summary["interface_tests"] = json.loads((evidence / "interface-tests.json").read_text())
        if summary["interface_tests"]["status"] != "passed":
            raise ValueError("Data boundary regression failed")
    reports[label] = summary
browser_dir = inputs / ("independent-test-ubuntu22-" + run)
browser = json.loads((browser_dir / "browser.json").read_text())
if browser["status"] != "passed":
    raise ValueError("Browser interaction acceptance is incomplete")
for kind in ("rpm", "deb"):
    packages = sorted((inputs / ("independent-packages-" + kind + "-" + run)).glob("*." + kind))
    if len(packages) != 2:
        raise ValueError("Exactly one center and one node package are required per format")
    for source in packages:
        shutil.copyfile(str(source), str(out / source.name))
for filename in ("dashboard.png", "dashboard-mobile.png", "report-preview.html"):
    shutil.copyfile(str(browser_dir / filename), str(out / filename))
for source, target in (("docs/development/BITS-INDEPENDENT.md", "ARCHITECTURE.md"),
                       ("docs/deployment/BITS-INDEPENDENT-PREVIEW.md", "OPERATIONS.md")):
    shutil.copyfile(str(ROOT / source), str(out / target))
sources = export_sources(out / ("bits-source-" + version + ".tar.gz"), commit)
verification = {
    "schema": "bits-independent-verification-v1", "version": version,
    "source_commit": commit, "workflow_run": run,
    "workflow_url": "https://github.com/" + os.environ["GITHUB_REPOSITORY"] + "/actions/runs/" + run,
    "scope": "Linux container userspaces with real systemd, HTTPS, SQLite and short stress processes",
    "hardware_readings": "synthetic provider; not real sckocp or license-server verification",
    "not_verified": ["physical hardware", "each distribution native kernel", "200 simultaneous physical nodes",
                     "multi-day soak", "production migration", "root-resistant native sckocp protection"],
    "linux": reports, "browser": browser, "sources": sources,
}
(out / "VERIFICATION.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
hashes = {}
for file in sorted(out.iterdir()):
    hashes[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
(out / "SHA256SUMS").write_text("".join(h + "  " + n + "\n" for n, h in hashes.items()))
print(json.dumps({"version": version, "source_commit": commit, "files": hashes}, indent=2))
