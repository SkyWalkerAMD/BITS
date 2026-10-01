"""Collect exact compiled Go dependency attribution inside Linux CI."""
import json
from pathlib import Path
import subprocess
import sys

destination = Path(sys.argv[1])
destination.mkdir(parents=True)
raw = subprocess.check_output(["go", "list", "-mod=readonly", "-deps", "-json", "./cmd/..."]).decode()
decoder = json.JSONDecoder()
modules = []
seen = set()
while raw.strip():
    package, offset = decoder.raw_decode(raw.lstrip())
    raw = raw.lstrip()[offset:]
    module = package.get("Module")
    if not module or module.get("Main") or module["Path"] in seen:
        continue
    seen.add(module["Path"])
    directory = Path(module["Dir"])
    licenses = [p for p in directory.iterdir()
                if p.is_file() and p.name.lower().startswith(("license", "copying", "notice"))]
    if not licenses:
        raise ValueError("Dependency attribution missing: " + module["Path"])
    target = destination / (module["Path"].replace("/", "_") + "@" + module["Version"])
    target.mkdir()
    for source in licenses:
        (target / source.name).write_bytes(source.read_bytes())
    modules.append({k: module[k] for k in ("Path", "Version", "Sum") if k in module})
(destination / "DEPENDENCIES.json").write_text(json.dumps(modules, indent=2))
goroot = Path(subprocess.check_output(["go", "env", "GOROOT"]).decode().strip())
(destination / "GO-LICENSE").write_bytes((goroot / "LICENSE").read_bytes())
