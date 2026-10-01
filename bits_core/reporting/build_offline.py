"""Cloud-only builder for the CPython 3.6 x86_64 report supplement."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.2.0"
NAME = "mon-sensors-report-py36-" + VERSION
PINS = {"numpy": "1.19.5", "pandas": "1.1.5", "openpyxl": "3.0.10",
        "XlsxWriter": "3.0.3", "python-dateutil": "2.8.2", "pytz": "2023.3",
        "six": "1.16.0", "et-xmlfile": "1.1.0"}
NATIVE_WHEELS = {
    "numpy": ("numpy-1.19.5-cp36-cp36m-manylinux2010_x86_64.whl",
              "a4646724fba402aa7504cd48b4b50e783296b5e10a524c7a6da62e4a8ac9698d"),
    "pandas": ("pandas-1.1.5-cp36-cp36m-manylinux1_x86_64.whl",
               "b61080750d19a0122469ab59b087380721d6b72a4e7d962e4d7e63e0c4504814"),
}


def get(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def patched_analyser(path):
    original = path.read_text(encoding="utf-8")
    return original.replace("aggfunc='mean')", "aggfunc='mean', dropna=False)").replace(
        "sheet_filename = sys.argv[3]\n",
        'sheet_filename = sys.argv[3] if len(sys.argv) > 3 else "Monitoring"\n')


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux":
        raise SystemExit("Build this supplement only in the authorized cloud Linux workflow")
    out = ROOT / "report-dist"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="report-build-") as temporary:
        stage = Path(temporary) / NAME
        payload = stage / "payload"
        payload.mkdir(parents=True)
        wheels = Path(temporary) / "wheels"
        wheels.mkdir()
        provenance = []
        lock = []
        for project, version in PINS.items():
            meta = json.loads(get("https://pypi.org/pypi/{}/{}/json".format(project, version)))
            candidates = []
            for entry in meta["urls"]:
                name = entry["filename"]
                if not name.endswith(".whl"):
                    continue
                if project in NATIVE_WHEELS:
                    if name != NATIVE_WHEELS[project][0]:
                        continue
                elif not name.endswith("-none-any.whl"):
                    continue
                candidates.append(entry)
            if len(candidates) != 1:
                raise ValueError("Expected exactly one pinned target wheel for " + project)
            entry = candidates[0]
            url = urllib.parse.urlparse(entry["url"])
            if url.scheme != "https" or url.hostname != "files.pythonhosted.org":
                raise ValueError("Unexpected package download origin")
            data = get(entry["url"])
            checksum = hashlib.sha256(data).hexdigest()
            if checksum != entry["digests"]["sha256"]:
                raise ValueError("Wheel SHA-256 mismatch: " + project)
            if project in NATIVE_WHEELS and checksum != NATIVE_WHEELS[project][1]:
                raise ValueError("Native wheel differs from the reviewed release: " + project)
            (wheels / entry["filename"]).write_bytes(data)
            lock.append("{}=={} --hash=sha256:{}".format(project, version, checksum))
            provenance.append({"project": project, "version": version, "filename": entry["filename"],
                               "url": entry["url"], "sha256": checksum})
        requirements = payload / "requirements.lock"
        requirements.write_text("\n".join(lock) + "\n", encoding="utf-8")
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                        "--no-index", "--find-links", str(wheels), "--require-hashes", "--no-deps",
                        "--only-binary=:all:", "--python-version", "3.6.8", "--implementation", "cp",
                        "--abi", "cp36m", "--platform", "manylinux2014_x86_64",
                        "--platform", "manylinux2010_x86_64", "--platform", "manylinux1_x86_64",
                        "--no-compile", "--target", str(payload / "vendor"), "-r", str(requirements)], check=True)
        for name in ("common.py", "report.py", "streaming.py"):
            shutil.copyfile(str(ROOT / "bits_core/reporting" / name), str(payload / name))
        for name in ("install.sh", "install.py", "README.md"):
            source = ROOT / ("docs/reports/OFFLINE-PY36.md" if name == "README.md" else "bits_core/reporting/" + name)
            shutil.copyfile(str(source), str(stage / name))
        source = ROOT / "integrations/mon-sensors/upstream-0.9.24a/mon-analyse-log.py"
        analysed = patched_analyser(source)
        # Keep provider labels, preserve unavailable columns, and write all labels as text.
        analysed = analysed.replace('numeric_columns = ["类型", "运行时长"',
                                    'numeric_columns = ["运行时长"')
        analysed = analysed.replace("engine='xlsxwriter')",
                                    "engine='xlsxwriter', options={'strings_to_formulas': False, 'strings_to_urls': False})")
        (payload / "analyser.py").write_text(analysed, encoding="utf-8")
        (payload / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        known = []
        for release in ("upstream", "upstream-0.9.24a"):
            base = ROOT / "integrations/mon-sensors" / release / "mon-analyse-log.py"
            known.append(hashlib.sha256(patched_analyser(base).encode("utf-8")).hexdigest())
        manifest = {"version": VERSION, "target": "linux-x86_64-cpython36",
                    "compatible_analyser_sha256": sorted(set(known)), "files": {}}
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                manifest["files"][str(path.relative_to(payload))] = hashlib.sha256(path.read_bytes()).hexdigest()
        (payload / "MANIFEST.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        archive = out / (NAME + ".tar.gz")
        with tarfile.open(str(archive), "w:gz") as bundle:
            for path in sorted(stage.rglob("*")):
                info = bundle.gettarinfo(str(path), arcname=str(path.relative_to(stage)))
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = 0
                info.mode = 0o755 if path.is_dir() or path.suffix == ".sh" else 0o644
                if path.is_file():
                    with path.open("rb") as stream:
                        bundle.addfile(info, stream)
                else:
                    bundle.addfile(info)
        spec = importlib.util.spec_from_file_location("project_build", str(ROOT / "build.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.self_extracting_package(archive, out / (NAME + ".run"), "install.sh")
        shutil.copyfile(str(stage / "README.md"), str(out / "README.md"))
        shutil.copyfile(str(payload / "PROVENANCE.json"), str(out / "PROVENANCE.json"))
        checksums = []
        for name in (NAME + ".run", NAME + ".tar.gz", "README.md", "PROVENANCE.json"):
            checksums.append(hashlib.sha256((out / name).read_bytes()).hexdigest() + "  " + name)
        (out / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="ascii")
        print(json.dumps({"artifact": str(out / (NAME + ".run")), "wheels": len(provenance)}))


if __name__ == "__main__":
    main()
