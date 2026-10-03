"""Assemble a formal release only from the same successful private Linux matrix."""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sys

assert sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution.source_export import export_sources

version = "0.4.1"
run = os.environ["GITHUB_RUN_ID"]
commit = os.environ["GITHUB_SHA"]
inputs = Path("native-evidence")
out = Path("native-release")
out.mkdir()
reports = {}
core = inputs / "core"
format_patch = next(core.rglob("native-format.patch"))
if format_patch.read_bytes():
    raise ValueError("Commit the reviewed formatting before releasing the tested source")
layout = json.loads(next(core.rglob("native-layout.json")).read_text())
if layout["status"] != "passed" or layout["source_commit"] != commit:
    raise ValueError("Repository layout validation is incomplete")
go_tests = next(core.rglob("native-tests.txt")).read_text()
test_names = re.findall(r"^--- PASS: (\S+)", go_tests, re.M)
if not test_names or re.search(r"^FAIL\b|^--- FAIL:", go_tests, re.M):
    raise ValueError("Go test evidence is incomplete")
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
upgrades = {}
for label, kind in (("rocky8", "rpm"), ("ubuntu22", "deb")):
    evidence = inputs / ("independent-upgrade-" + label + "-" + run)
    summary = json.loads((evidence / "upgrade.json").read_text())
    if (summary["status"], summary["from"], summary["to"], summary["format"]) != ("passed", "0.4.0", version, kind):
        raise ValueError("Package upgrade verification failed: " + label)
    summary["image"] = (evidence / "image.txt").read_text().strip()
    upgrades[label] = summary
for kind in ("rpm", "deb"):
    packages = sorted((inputs / ("independent-packages-" + kind + "-" + run)).glob("*." + kind))
    expected = {"bits-" + role + ("-0.4.1-1.el8.x86_64.rpm" if kind == "rpm" else "_0.4.1-1_amd64.deb") for role in ("center", "node")}
    if {p.name for p in packages} != expected:
        raise ValueError("Exactly one center and one node package are required per format")
    for source in packages:
        shutil.copyfile(str(source), str(out / source.name))
for filename in ("dashboard.png", "dashboard-mobile.png", "batch-running.png", "batch-wizard.png", "report-preview.html",
                 "hardware-monitor.png", "hardware-table.png", "hardware-mobile.png",
                 "hardware-scrolled.png", "workspace-compact.png", "navigation-mobile.png",
                 "dispatch-workspace.png", "dispatch-group.png", "dispatch-mobile.png",
                 "bmc-overview.png", "bmc-nodes-mobile.png", "node-template.png", "node-template-mobile.png",
                 "hardware-idle.png", "hardware-idle-mobile.png",
                 "wake-confirm.png", "wake-progress.png", "wake-mobile.png", "delete-confirm.png", "delete-mobile.png"):
    shutil.copyfile(str(browser_dir / filename), str(out / filename))
for source, target in (("docs/development/BITS-INDEPENDENT.md", "ARCHITECTURE.md"),
                       ("docs/deployment/BITS-INDEPENDENT.md", "OPERATIONS.md"),
                       ("docs/releases/0.4.1.md", "RELEASE-NOTES.md")):
    shutil.copyfile(str(ROOT / source), str(out / target))
sources = export_sources(out / ("bits-source-" + version + ".tar.gz"), commit)
verification = {
    "schema": "bits-independent-verification-v1", "version": version,
    "channel": "stable", "tag": "v" + version,
    "source_commit": commit, "workflow_run": run,
    "validation_environment": "private GitHub Actions Linux; run identifier retained above",
    "scope": "Linux container userspaces with real systemd, HTTPS, SQLite and short stress processes",
    "hardware_readings": "synthetic provider; not real sckocp or license-server verification",
    "not_verified": ["physical BMC/IPMI reachability and boot", "physical hardware", "each distribution native kernel", "200 simultaneous physical nodes",
                     "multi-day soak", "production migration", "root-resistant native sckocp protection"],
    "linux": reports, "browser": browser, "sources": sources, "upgrades": upgrades,
    "core": {"status": "passed", "go_tests": test_names, "race": True, "vet": True,
             "repository_layout": layout, "source_export_tests": "passed"},
}
(out / "VERIFICATION.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
hashes = {}
for file in sorted(out.iterdir()):
    hashes[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
(out / "SHA256SUMS").write_text("".join(h + "  " + n + "\n" for n, h in hashes.items()))
print(json.dumps({"version": version, "source_commit": commit, "files": hashes}, indent=2))
