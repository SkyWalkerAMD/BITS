"""Sealed task uploads and receiver-verified acknowledgements before local retention."""
import argparse
import hashlib
import json
import logging
import os
import shutil
import stat
import time

from .common import capture, identifier, read_json, utc_timestamp, write_json

LOG = logging.getLogger("ocrun.logs")


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_file(directory, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("Invalid log member")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Unsafe log member")
    current = os.path.abspath(directory)
    if os.path.islink(current):
        raise ValueError("Log directory cannot be a symlink")
    for part in parts:
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise ValueError("Log member cannot traverse symlinks")
    if not stat.S_ISREG(os.stat(current).st_mode):
        raise ValueError("Log member must be a regular file")
    return current


def seal(directory):
    task_id = identifier(os.path.basename(directory))
    # A receiver requires the final result to be covered by the manifest.
    safe_file(directory, "result.json")
    files = []
    for parent, directories, names in os.walk(directory, followlinks=False):
        if any(os.path.islink(os.path.join(parent, name)) for name in directories):
            raise ValueError("Task logs contain a directory symlink")
        for name in sorted(names):
            relative = os.path.relpath(os.path.join(parent, name), directory).replace(os.sep, "/")
            if relative == "manifest.json" or name.startswith(".ocrun-"):
                continue
            path = safe_file(directory, relative)
            files.append({"path": relative, "bytes": os.path.getsize(path), "sha256": digest(path)})
    if len(files) > 10000:
        raise ValueError("Too many task log files")
    write_json(os.path.join(directory, "manifest.json"), {"schema_version": 1, "task_id": task_id, "files": files})


def verify_directory(directory, exact_members=False):
    manifest_path = safe_file(directory, "manifest.json")
    if os.path.getsize(manifest_path) > 4 * 1024 * 1024:
        raise ValueError("Manifest too large")
    manifest_hash = digest(manifest_path)
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("task_id") != identifier(os.path.basename(directory)):
        raise ValueError("Manifest identity mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 10000:
        raise ValueError("Invalid manifest file list")
    names = set()
    for entry in files:
        relative = entry["path"]
        if relative in names or relative == "manifest.json":
            raise ValueError("Duplicate or recursive log member")
        names.add(relative)
        path = safe_file(directory, relative)
        if os.path.getsize(path) != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError("Uploaded log checksum mismatch: " + relative)
    if "result.json" not in names or "task.json" not in names:
        raise ValueError("Final task metadata missing")
    if exact_members:
        current = set()
        for parent, directories, files_in_directory in os.walk(directory, followlinks=False):
            if any(os.path.islink(os.path.join(parent, name)) for name in directories):
                raise ValueError("Unsealed log directory symlink")
            for name in files_in_directory:
                relative = os.path.relpath(os.path.join(parent, name), directory).replace(os.sep, "/")
                if relative != "manifest.json" and not name.startswith(".ocrun-"):
                    current.add(relative)
        if current != names:
            raise ValueError("Task log members changed after sealing")
    if read_json(safe_file(directory, "task.json")).get("id") != manifest["task_id"]:
        raise ValueError("Task metadata identity mismatch")
    if digest(manifest_path) != manifest_hash:
        raise ValueError("Manifest changed during verification")
    return manifest_hash


def verify_server(config, redis=None):
    from .queue import Queue
    from .rediswire import Redis
    redis = redis or Redis(config["redis"])
    verified, pending = 0, 0
    log_root = os.path.join(config["data_root"], "logs")
    for filename in sorted(os.listdir(config["enrollments"])):
        if not filename.endswith(".json"):
            continue
        host = identifier(filename[:-5])
        root = os.path.join(log_root, host)
        if not os.path.isdir(root) or os.path.islink(root):
            continue
        queue = Queue(redis, host)
        for task_id in os.listdir(root):
            directory = os.path.join(root, task_id)
            if not os.path.isdir(directory) or os.path.islink(directory):
                continue
            try:
                identifier(task_id)
                manifest_path = safe_file(directory, "manifest.json")
                if os.path.getsize(manifest_path) > 4 * 1024 * 1024:
                    raise ValueError("Manifest too large")
                manifest_hash = digest(manifest_path)
                key = queue.key("log_ack:" + task_id)
                raw = redis.execute("GET", key)
                if raw and json.loads(raw).get("manifest_sha256") == manifest_hash:
                    continue
                task = queue.get(task_id)
                if not task or task["status"] in ("pending", "running"):
                    pending += 1
                    continue
                verified_hash = verify_directory(directory)
                if read_json(safe_file(directory, "result.json")).get("status") != task["status"]:
                    raise ValueError("Uploaded result disagrees with acknowledged task status")
                redis.execute("SET", key, json.dumps({"manifest_sha256": verified_hash, "verified_at": utc_timestamp()}))
                verified += 1
            except (OSError, ValueError, KeyError, TypeError):
                pending += 1
                LOG.warning("Logs not yet verified: %s/%s", host, task_id)
    return {"verified": verified, "pending": pending}


class LogStore:
    def __init__(self, config, queue):
        self.config, self.queue = config, queue
        self.root = config.get("log_dir", "/var/log/ocrun")
        self.receipts = os.path.join(config.get("state_dir", "/var/lib/ocrun-agent"), "log-receipts")
        os.makedirs(self.receipts, exist_ok=True)

    def upload(self, active_id=None):
        settings = self.config.get("upload")
        if not settings:
            return {"error": "Upload not configured", "pending": 0, "verified": 0}
        destination = "rsync://{host_id}@{host}:{port}/logs-{host_id}/".format(
            host_id=identifier(self.config["host_id"]), host=settings["host"], port=int(settings.get("port", 1873)))
        env = dict(os.environ, RSYNC_PASSWORD=settings["password"])
        pending, verified, error = 0, 0, None
        deadline = time.monotonic() + 120
        if not os.path.isdir(self.root):
            return {"error": None, "pending": 0, "verified": 0}
        for task_id in sorted(os.listdir(self.root), key=lambda item: (item != active_id, item)):
            if time.monotonic() >= deadline:
                break
            directory = os.path.join(self.root, task_id)
            if not os.path.isdir(directory) or os.path.islink(directory):
                continue
            try:
                identifier(task_id)
            except ValueError:
                continue
            manifest_path = os.path.join(directory, "manifest.json")
            sealed = os.path.isfile(manifest_path) and task_id != active_id
            manifest_hash = digest(manifest_path) if sealed else None
            receipt_path = os.path.join(self.receipts, task_id + ".json")
            receipt = read_json(receipt_path) if os.path.isfile(receipt_path) else {}
            acknowledged = False
            if sealed:
                days = self.config.get("local_retention_days")
                expired = finite_days(days) and time.time() - os.path.getmtime(manifest_path) >= days * 86400
                if receipt.get("manifest_sha256") == manifest_hash and receipt.get("verified_at") and not expired:
                    verified += 1
                    continue
                raw = self.queue.redis.execute("GET", self.queue.key("log_ack:" + task_id))
                ack = json.loads(raw) if raw else {}
                acknowledged = ack.get("manifest_sha256") == manifest_hash
                if acknowledged:
                    verified += 1
                    receipt.update(manifest_sha256=manifest_hash, verified_at=ack["verified_at"])
                    write_json(receipt_path, receipt)
                    if expired:
                        # Never follow symlinks or delete active/unacknowledged logs.
                        if (os.path.realpath(directory) == os.path.join(os.path.realpath(self.root), task_id)
                                and verify_directory(directory, exact_members=True) == manifest_hash):
                            shutil.rmtree(directory)
                    continue
            pending += 1
            if sealed and receipt.get("manifest_sha256") == manifest_hash and time.time() - receipt.get("transferred_epoch", 0) < 300:
                continue
            args = ["rsync", "-rt", "--partial", "--timeout=30", "--contimeout=10", "--exclude=.ocrun-*"]
            if sealed:
                args.append("--checksum")
            code, _, _ = capture(args + [directory, destination], timeout=120, env=env)
            if code:
                error = "Log upload failed; local files retained"
            elif sealed:
                write_json(receipt_path, {"manifest_sha256": manifest_hash, "transferred_epoch": time.time()})
        return {"error": error, "pending": pending, "verified": verified}


def finite_days(value):
    from .safety import finite
    return finite(value) and value >= 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-config", default="/etc/ocrun/server.json")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = read_json(args.server_config)
    while True:
        try:
            print(json.dumps(verify_server(config)), flush=True)
        except Exception:
            if not args.watch:
                raise
            LOG.exception("Log verification will retry")
        if not args.watch:
            break
        time.sleep(30)


if __name__ == "__main__":
    main()
