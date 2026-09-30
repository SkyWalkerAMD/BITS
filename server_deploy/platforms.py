"""Explicit server OS/package profiles. A recognized OS is not a test result."""
import argparse
import json
import shlex


MATRIX = (
    ("rocky8", "rockylinux/rockylinux:8"),
    ("rocky9", "rockylinux/rockylinux:9"),
    ("rocky10", "rockylinux/rockylinux:10"),
    ("alma8", "almalinux:8"),
    ("alma9", "almalinux:9"),
    ("alma10", "almalinux:10"),
    ("ubuntu22", "ubuntu:22.04"),
    ("ubuntu24", "ubuntu:24.04"),
    ("ubuntu26", "ubuntu:26.04"),
    ("debian11", "debian:11"),
    ("debian12", "debian:12"),
    ("debian13", "debian:13"),
)


def read_release(path="/etc/os-release"):
    values = {}
    with open(path, encoding="utf-8") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            tokens = shlex.split(value, comments=True)
            if len(tokens) > 1:
                raise ValueError("Invalid os-release field: " + key)
            values[key] = tokens[0] if tokens else ""
    return values


def profile(values):
    distro, version = values.get("ID", ""), values.get("VERSION_ID", "")
    major = version.split(".")[0]
    el = distro in ("rocky", "almalinux", "rhel") and major in ("8", "9", "10")
    ubuntu = distro == "ubuntu" and version in ("22.04", "24.04", "26.04")
    debian = distro == "debian" and version in ("11", "12", "13")
    if not (el or ubuntu or debian):
        raise ValueError("Unsupported server OS: {} {} (EL 8-10, Ubuntu 22/24/26 LTS, Debian 11-13 required)".format(distro, version))
    valkey = el and major == "10"
    database = "valkey" if valkey else "redis"
    packages = ["python3", "nginx", "rsync", "ca-certificates", "tar", "gzip", "util-linux", "nftables"]
    packages += ([database, "iproute", "policycoreutils-python-utils", "selinux-policy-targeted"] if el
                 else ["redis-server", "redis-tools", "iproute2"])
    return {
        "id": distro, "version": version, "family": "el" if el else "deb",
        "package_manager": "dnf" if el else "apt-get", "packages": packages,
        "module": "redis:6" if el and major == "8" else None,
        "database": database, "database_user": database,
        "database_binary": "/usr/bin/{}-server".format(database),
        "database_cli": "/usr/bin/{}-cli".format(database),
        "database_home": "/var/lib/" + database,
        "subscription_required": distro == "rhel",
        "recognition_only": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--os-release", default="/etc/os-release")
    parser.add_argument("--matrix", action="store_true")
    args = parser.parse_args()
    value = {"include": [dict(label=label, image=image) for label, image in MATRIX]} if args.matrix else profile(read_release(args.os_release))
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
