"""Cloud-only release builder. It does not execute input node programs."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

from bits_core.center import VERSION

ROOT = Path(__file__).resolve().parents[2]


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Build this project in the authorized GitHub Actions Linux environment")
    files = list((ROOT / "bits_core/center").glob("*.py")) + list((ROOT / "bits_core/center").glob("*.sh"))
    files += list((ROOT / "bits_core/center").glob("*.md"))
    contents = {p.relative_to(ROOT).as_posix(): p.read_bytes().replace(b"\r\n", b"\n") for p in files if p.name not in ("build.py", "release.py", "test_server.py")}
    contents['bits_core/__init__.py'] = (ROOT / 'bits_core/__init__.py').read_bytes()
    for name in ('bits_layout.py', 'bits_core/layout.py'):
        contents[name] = (ROOT / name).read_bytes()
    for source, target in (("docs/deployment/SERVER.md", "bits_core/center/MANUAL.md"),
                           ("docs/archive/server-0.1.2/ACCEPTANCE.md", "bits_core/center/ACCEPTANCE.md")):
        contents[target] = (ROOT / source).read_bytes().replace(b"\r\n", b"\n")
    manifest = {"version": VERSION, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT)).decode().strip(),
                "protocol": "ocrun-legacy-v1", "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in contents.items()}}
    contents["PACKAGE.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output = ROOT / "server-dist"
    output.mkdir(exist_ok=True)
    archive = output / ("bits-center-runtime-" + VERSION + ".tar.gz")
    with tarfile.open(str(archive), "w:gz") as package:
        for name, data in sorted(contents.items()):
            member = tarfile.TarInfo("bits-center-runtime-" + VERSION + "/" + name)
            member.size = len(data)
            member.mode = 0o755 if name.endswith(".sh") else 0o644
            package.addfile(member, io.BytesIO(data))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(sha + "  " + archive.name + "\n")
    print(json.dumps({"archive": archive.name, "sha256": sha, "bytes": archive.stat().st_size, "commit": manifest["source_commit"]}))


if __name__ == "__main__":
    main()
