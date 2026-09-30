"""Extract only regular files/directories and internal symlinks into an empty target."""
import argparse
import hashlib
import os
import pathlib
import shutil
import tarfile


def unpack(archive, destination, digest):
    checksum = hashlib.sha256()
    with open(archive, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    if checksum.hexdigest() != digest.lower():
        raise ValueError("Tool archive checksum mismatch")
    destination = os.path.abspath(destination)
    if os.path.exists(destination) and os.listdir(destination):
        raise ValueError("Destination must be empty; live tools are never overwritten")
    os.makedirs(destination, exist_ok=True)
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        names, links = set(), []
        # Validate the complete archive before writing any member.
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in member.name or ":" in member.name:
                raise ValueError("Unsafe archive path")
            if member.name in names:
                raise ValueError("Duplicate archive entry")
            names.add(member.name)
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("Unsupported archive member")
            if member.issym():
                link = pathlib.PurePosixPath(member.linkname)
                resolved = os.path.normpath(os.path.join(destination, *path.parent.parts, member.linkname))
                if link.is_absolute() or "\\" in member.linkname or ":" in member.linkname or os.path.commonpath([destination, resolved]) != destination:
                    raise ValueError("Unsafe symlink")
                links.append(member)
        link_names = {pathlib.PurePosixPath(member.name) for member in links}
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if any(parent in link_names for parent in path.parents):
                raise ValueError("Archive writes through a symlink")
        for member in members:
            target = os.path.join(destination, *pathlib.PurePosixPath(member.name).parts)
            if member.isdir():
                os.makedirs(target, exist_ok=True)
            elif member.isfile():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with bundle.extractfile(member) as source, open(target, "xb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, member.mode & 0o777)
        for member in links:
            target = os.path.join(destination, *pathlib.PurePosixPath(member.name).parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.symlink(member.linkname, target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("destination")
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    unpack(args.archive, args.destination, args.sha256)


if __name__ == "__main__":
    main()
