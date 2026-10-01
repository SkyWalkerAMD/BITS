#!/usr/bin/env python3
"""Build installable source and a curated tool archive without executing input code."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import tarfile
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
VERSION = "0.12.8"
PLUGIN_VERSION = "0.14.0"
PUBLIC_API_VERSION = "0.4.0"
API_CORE_FILES = ("sckocp_api/__init__.py", "sckocp_api/interface.py", "sckocp_api/provider.py",
                  "sckocp_api/security.py")
API_FILES = API_CORE_FILES + ("sckocp_api/__main__.py", "sckocp_api/cli.py", "sckocp_api/install.py",
                              "sckocp_api/bootstrap.py")
BOOTSTRAP_FILES = ("installer-python.sh",)
DOC_PATHS = {
    "FLEET.md": "docs/archive/agent-0.12.8/FLEET.md",
    "OPERATIONS.md": "docs/archive/agent-0.12.8/OPERATIONS.md",
    "SCKOCP.md": "docs/archive/agent-0.12.8/SCKOCP.md",
    "PACKAGES.md": "docs/archive/monitoring-0.3.1/PACKAGES.md",
    "README-EXPERIMENTAL.md": "docs/archive/agent-0.12.8/README.md",
    "DISTRIBUTION.md": "docs/deployment/DISTRIBUTION.md",
    "SCKOCP-API.md": "docs/monitoring/SCKOCP-API.md",
    "MON-SENSORS.md": "docs/monitoring/MON-SENSORS.md",
    "docs/API-SECURITY.md": "docs/security/API-SECURITY.md",
}
TOOL_DIRS = {"stress", "stress-ng", "bc", "mlc", "mbw", "sysjitter", "ptu", "unixbench"}


def document_bytes(source, destination, documents):
    """Keep links usable when repository documents become flat package manuals.

    documents maps canonical repository paths to archive paths. Links to files
    outside this package point to the tested revision in the private repository.
    Only Markdown destinations are rewritten; command examples stay unchanged.
    """
    text = (ROOT / source).read_text(encoding="utf-8")
    revision = os.environ.get("GITHUB_SHA", "main")
    if not re.fullmatch(r"[0-9a-f]{40}|main", revision):
        raise ValueError("Invalid documentation source revision")
    def replace(match):
        url = urlsplit(match.group(2))
        if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
            return match.group(0)
        canonical = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(url.path)))
        if canonical in documents:
            target = posixpath.relpath(documents[canonical], posixpath.dirname(destination) or ".")
        else:
            target = "https://github.com/SkyWalkerAMD/ocrun-next/blob/" + revision + "/" + quote(canonical, safe="/")
        if url.query:
            target += "?" + url.query
        if url.fragment:
            target += "#" + url.fragment
        return match.group(1) + target + ")"
    return re.sub(r"(\[[^\]\n]*\]\()([^\s)]+)\)", replace, text).encode("utf-8")


def package_bytes(name, files):
    source = DOC_PATHS.get(name, name)
    if name.endswith(".md"):
        documents = {DOC_PATHS.get(n, n): n for n in files if n.endswith(".md")}
        return document_bytes(source, name, documents)
    return (ROOT / source).read_bytes().replace(b"\r\n", b"\n")


def metadata(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def source_package(output):
    with tarfile.open(output, "w:gz") as bundle:
        paths = [ROOT / name for name in ("install-server.sh", "install-client.sh", "install-deps.sh", "install-mon-sensors-plugin.sh", "install-sckocp-api.sh", "installer-python.sh", "mon-sensors-plugin", "sckocp-api", "README.md", "FLEET.md", "OPERATIONS.md", "SCKOCP.md", "SCKOCP-API.md", "MON-SENSORS.md", "PACKAGES.md")]
        paths += [ROOT / "README-EXPERIMENTAL.md", ROOT / "DISTRIBUTION.md"]
        paths += sorted((ROOT / "ocrun").glob("*.py"))
        paths += sorted((ROOT / "bits_core/collector").glob("*.py"))
        paths += [ROOT / name for name in API_FILES]
        paths += sorted((ROOT / "systemd").glob("*.service"))
        files = [p.relative_to(ROOT).as_posix() for p in paths]
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            # Repository documentation is grouped under docs; preserve the
            # established archive member names for existing installers/users.
            data = package_bytes(relative, files)
            member = tarfile.TarInfo(relative)
            member.size = len(data)
            member.mode = 0o755 if path.suffix == ".sh" or path.name in ("mon-sensors-plugin", "sckocp-api") else 0o644
            bundle.addfile(member, io.BytesIO(data))


def mon_sensors_package(output):
    files = ("install-mon-sensors-plugin.sh", "mon-sensors-plugin", "MON-SENSORS.md", "SCKOCP-API.md", "PACKAGES.md",
             "bits_core/collector/__init__.py", "bits_core/collector/collector.py", "bits_core/collector/install.py",
             "bits_core/collector/runtime.py", "bits_core/collector/adoption.py",
             "bits_core/collector/legacy_runtime.py")
    source_files_package(output, files + ('bits_layout.py', 'bits_core/__init__.py', 'bits_core/layout.py') + API_CORE_FILES + BOOTSTRAP_FILES)


def sckocp_api_package(output):
    source_files_package(output, ("ocrun/__init__.py", "ocrun/sckocp.py", "SCKOCP.md", "SCKOCP-API.md") + API_CORE_FILES)


def public_api_package(output):
    source_files_package(output, ("sckocp-api", "install-sckocp-api.sh", "SCKOCP-API.md",
                                  "docs/API-SECURITY.md", "examples/read-sckocp.py") + API_FILES + BOOTSTRAP_FILES)


def source_files_package(output, files):
    with tarfile.open(output, "w:gz") as bundle:
        for name in files:
            data = package_bytes(name, files)
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o755 if name.endswith(".sh") or name in ("mon-sensors-plugin", "sckocp-api") else 0o644
            bundle.addfile(member, io.BytesIO(data))


def self_extracting_package(archive, output, installer):
    """Wrap an already-built archive; do not run either archive or installer."""
    payload = archive.read_bytes()
    template = (ROOT / "templates/self-extract.sh.in").read_text(encoding="utf-8")
    template = (template.replace("@PRODUCT@", output.stem)
                .replace("@INSTALLER@", installer)
                .replace("@BYTES@", str(len(payload)))
                .replace("@SHA256@", hashlib.sha256(payload).hexdigest()))
    offset = 1
    while True:
        header = template.replace("@OFFSET@", str(offset)).encode("utf-8")
        expected = len(header) + 1  # GNU tail uses a one-based byte offset.
        if expected == offset:
            break
        offset = expected
    output.write_bytes(header + payload)
    output.chmod(0o755)


def tools_package(archive, output, include_spec=False):
    count, skipped = 0, []
    seen = set()
    with tarfile.open(archive, "r|gz") as source, tarfile.open(output, "w:gz", compresslevel=1) as target:
        for member in source:
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2 or parts[0] != "ocrun":
                continue
            spec = include_spec and parts[1] == "cpu2017"
            native = (len(parts) >= 3 and parts[1] == "bin" and
                      (parts[2] in TOOL_DIRS or re.match(r"^p95-(no|avx|fma3|avx512)_m(1|2|4)$", parts[2])))
            if not (spec or native):
                continue
            if (".." in parts or ":" in member.name or "\\" in member.name or
                    any(p == ".DS_Store" or p.startswith(".#") for p in parts) or
                    member.name.endswith((".pid", ".log"))):
                continue
            name = "/".join(parts[1:])
            if name in seen:
                continue
            if member.issym():
                link = member.linkname
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
                if link.startswith("/") or resolved.startswith("../") or ":" in link or "\\" in link:
                    skipped.append(name)
                    continue
            elif not (member.isfile() or member.isdir()):
                skipped.append(name)
                continue
            member.name, member.uid, member.gid, member.uname, member.gname = name, 0, 0, "root", "root"
            member.mode &= 0o777
            stream = source.extractfile(member) if member.isfile() else None
            if name == "bin/bc/bcr":
                stream.close()
                data = b'#!/bin/bash\nset -euo pipefail\nfor digits in 14000 16000 20000; do\n  echo "BC digits=$digits"\n  time bc -l -q <<< "scale=$digits; 4*a(1)" >/dev/null\ndone\n'
                member.size = len(data)
                stream = io.BytesIO(data)
            target.addfile(member, stream)
            if stream:
                stream.close()
            seen.add(name)
            count += 1
    return {"entries": count, "skipped_unsafe_links": skipped, "includes_spec": include_spec}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", help="Original 0730 tar.gz; optional when only rebuilding runtime")
    parser.add_argument("--include-spec", action="store_true", help="Include the large CPU2017 suite")
    parser.add_argument("--output", default=str(ROOT / "dist"))
    args = parser.parse_args()
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    runtime = destination / ("ocrun-runtime-" + VERSION + ".tar.gz")
    source_package(runtime)
    release = {"version": VERSION, "plugin_version": PLUGIN_VERSION, "runtime": metadata(runtime)}
    overlay = destination / ("mon-sensors-plugin-" + PLUGIN_VERSION + ".tar.gz")
    mon_sensors_package(overlay)
    release["mon_sensors_plugin"] = metadata(overlay)
    api = destination / ("ocrun-sckocp-api-" + VERSION + ".tar.gz")
    sckocp_api_package(api)
    release["sckocp_api"] = metadata(api)
    public_api = destination / ("sckocp-api-" + PUBLIC_API_VERSION + ".tar.gz")
    public_api_package(public_api)
    release["public_api"] = metadata(public_api)
    for kind, archive, installer in (
            ("public_api_installer", public_api, "install-sckocp-api.sh"),
            ("mon_sensors_installer", overlay, "install-mon-sensors-plugin.sh")):
        executable = destination / (archive.name[:-len(".tar.gz")] + ".run")
        self_extracting_package(archive, executable, installer)
        release[kind] = metadata(executable)
    if args.archive:
        archive = destination / ("ocrun-tools-" + VERSION + ".tar.gz")
        details = tools_package(args.archive, archive, args.include_spec)
        release["tools"] = dict(metadata(archive), **details)
    elif (destination / "manifest.json").exists():
        previous = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        previous_tools = previous.get("tools")
        if previous.get("version") == VERSION and previous_tools:
            tool_path = destination / previous_tools["file"]
            current = metadata(tool_path)
            if current["sha256"] != previous_tools["sha256"]:
                raise ValueError("Existing tools changed; rebuild from the original archive")
            release["tools"] = previous_tools
    (destination / "manifest.json").write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(release, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
