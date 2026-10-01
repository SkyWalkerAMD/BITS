"""Collect exact compiled Go dependency attribution inside Linux CI."""
import json
from pathlib import Path
import subprocess
import sys

destination = Path(sys.argv[1])
destination.mkdir(parents=True)
raw = subprocess.check_output(["go", "list", "-mod=readonly", "-m", "-json", "all"]).decode()
decoder = json.JSONDecoder()
modules = []
while raw.strip():
    module, offset = decoder.raw_decode(raw.lstrip())
    raw = raw.lstrip()[offset:]
    if module.get("Main"):
        continue
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
