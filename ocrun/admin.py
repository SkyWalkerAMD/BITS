import argparse
import glob
import json
import os
import secrets
import subprocess
import time
import uuid

from .common import exclusive_lock, identifier, read_json, write_json
from .dashboard import write_dashboard, write_fleet_csv
from .fleet import FleetStore, batch_summary, batch_tick, run_batch
from .logs import verify_server
from .provision import render_rsync
from .queue import Queue
from .rediswire import Redis
from .workloads import validate_task


def enroll(config, redis, host, output):
    identifier(host)
    enrollments = config["enrollments"]
    os.makedirs(enrollments, exist_ok=True)
    record_path = os.path.join(enrollments, host + ".json")
    with exclusive_lock(os.path.join(enrollments, ".lock")):
        if os.path.exists(record_path):
            raise ValueError("Host already enrolled; reuse its existing configuration")
        password = secrets.token_hex(32)
        node = {"host_id": host,
                "redis": {"host": config["address"], "port": 6380, "username": "node_" + host, "password": password},
                "upload": {"host": config["address"], "port": 1873, "password": secrets.token_hex(32)},
                "tool_root": "/opt/ocrun/tools", "state_dir": "/var/lib/ocrun-agent",
                "log_dir": "/var/log/ocrun", "sample_interval_seconds": 5,
                "offline_grace_seconds": 120, "upload_interval_seconds": 60,
                "cancel_poll_seconds": 2, "local_retention_days": None,
                "protection": {}, "acceptance": {},
                "required_metrics": ["cpu_temperature_c"], "sensors": {}}
        redis.execute("ACL", "SETUSER", "node_" + host, "reset", "on", ">" + password,
                      "~ocrun:h:" + host + ":*", "+get", "+set", "+del", "+hget", "+hset", "+hgetall",
                      "+hexists", "+lindex", "+llen", "+lpop", "+rpush", "+eval", "+ping", "+expire")
        redis.execute("ACL", "SAVE")
        log_path = os.path.join(config["data_root"], "logs", host)
        os.makedirs(log_path, exist_ok=True)
        import pwd
        account = pwd.getpwnam("ocrun-logs")
        os.chown(log_path, account.pw_uid, account.pw_gid)
        os.chmod(log_path, 0o750)
        write_json(record_path, node)
        records = [read_json(path) for path in sorted(glob.glob(os.path.join(enrollments, "*.json")))]
        render_rsync("/", config, records)
        # rsync rereads its configuration for new connections; no active uploads are stopped.
        write_json(output, node)
    print("Client configuration written to " + os.path.abspath(output))


def select_hosts(store, args):
    host = getattr(args, "host", None)
    group = getattr(args, "group", None)
    if host and group:
        raise ValueError("Choose either a host or a group")
    if host:
        hosts = store.check_hosts([host])
    elif group:
        hosts = store.group(group)
    else:
        hosts = store.enrolled_hosts()
    offset, limit = getattr(args, "host_offset", 0), getattr(args, "host_limit", 200)
    if offset < 0 or not 1 <= limit <= 1000:
        raise ValueError("host-offset must be nonnegative; host-limit must be 1..1000")
    return hosts[offset:offset + limit], len(hosts)


def status_records(redis, hosts, offset=0, limit=20, status=None):
    records = []
    for host in hosts:
        queue = Queue(redis, host)
        raw = redis.execute("GET", queue.key("heartbeat"))
        page = queue.task_page(offset, limit, status)
        records.append(dict(page, host=host, online=bool(raw),
                            heartbeat=json.loads(raw) if raw else None,
                            running=queue.running(), paused=queue.is_paused(),
                            pending_count=redis.execute("LLEN", queue.key("pending"))))
    return records


def _selection_arguments(parser):
    parser.add_argument("host", nargs="?")
    parser.add_argument("--group")
    parser.add_argument("--host-offset", type=int, default=0)
    parser.add_argument("--host-limit", type=int, default=200)


def main():
    parser = argparse.ArgumentParser(description="OCRUN center management")
    parser.add_argument("--config", default="/etc/ocrun/server.json")
    commands = parser.add_subparsers(dest="action")
    commands.add_parser("verify-logs", help="Verify received log manifests and acknowledge complete uploads")
    register = commands.add_parser("enroll")
    register.add_argument("host")
    register.add_argument("--output", required=True)
    create = commands.add_parser("enqueue")
    create.add_argument("host")
    create.add_argument("tool", nargs="?")
    create.add_argument("--template")
    create.add_argument("--profile", help="JSON object with protection and/or acceptance settings")
    create.add_argument("--seconds", type=int)
    create.add_argument("--threads", type=int)
    create.add_argument("--memory-mb", type=int)
    create.add_argument("--id", help="Supply an ID for idempotent submission")
    show = commands.add_parser("status")
    _selection_arguments(show)
    show.add_argument("--offset", type=int, default=0)
    show.add_argument("--limit", type=int, default=20)
    show.add_argument("--state", help="Filter within each bounded history page")
    for action in ("dashboard", "export"):
        snapshot = commands.add_parser(action)
        _selection_arguments(snapshot)
        snapshot.add_argument("--offset", type=int, default=0)
        snapshot.add_argument("--limit", type=int, default=20)
        snapshot.add_argument("--output", required=True)
    for action in ("pause", "resume", "reindex-history"):
        command_parser = commands.add_parser(action)
        _selection_arguments(command_parser)
    cancel = commands.add_parser("cancel")
    cancel.add_argument("host")
    cancel.add_argument("task_id")
    group = commands.add_parser("group-set", help="Create or replace a named device group")
    group.add_argument("name")
    group.add_argument("hosts", nargs="+")
    template = commands.add_parser("template-set", help="Create or replace a workload template")
    template.add_argument("name")
    template.add_argument("--file", required=True)
    batch = commands.add_parser("batch-create")
    batch.add_argument("batch_id")
    selection = batch.add_mutually_exclusive_group(required=True)
    selection.add_argument("--hosts", nargs="+")
    selection.add_argument("--group")
    batch.add_argument("--template", required=True)
    batch.add_argument("--max-parallel", type=int, default=1)
    batch.add_argument("--stagger-seconds", type=float, default=0)
    controller = commands.add_parser("batch-run", help="Run/resume the foreground batch controller")
    controller.add_argument("batch_id")
    controller.add_argument("--once", action="store_true")
    controller.add_argument("--poll-seconds", type=float, default=5)
    for action in ("batch-status", "batch-cancel"):
        commands.add_parser(action).add_argument("batch_id")
    resolve = commands.add_parser("resolve-interrupted", help="Only after checking no workload remains on the host")
    resolve.add_argument("host")
    resolve.add_argument("task_id")
    resolve.add_argument("--confirmed-stopped", action="store_true", required=True)
    args = parser.parse_args()
    if not args.action:
        parser.error("Choose a command")
    config = read_json(args.config)
    redis = Redis(config["redis"])
    store = FleetStore(config)
    if args.action == "verify-logs":
        print(json.dumps(verify_server(config, redis), ensure_ascii=False, indent=2))
        return
    if args.action == "enroll":
        enroll(config, redis, args.host, args.output)
        return
    if args.action in ("status", "dashboard", "export", "pause", "resume", "reindex-history"):
        hosts, total_hosts = select_hosts(store, args)
        if args.action in ("pause", "resume", "reindex-history"):
            for host in hosts:
                queue = Queue(redis, host)
                result = getattr(queue, args.action.replace("-", "_"))()
                print(json.dumps({"host": host, "action": args.action, "result": result}))
            print(json.dumps({"selected_hosts": len(hosts), "total_hosts": total_hosts,
                              "host_offset": args.host_offset}))
            return
        records = status_records(redis, hosts, args.offset, args.limit, getattr(args, "state", None))
        if args.action == "dashboard":
            write_dashboard(args.output, records, os.path.join(config["data_root"], "logs"))
        elif args.action == "export":
            write_fleet_csv(args.output, records)
        else:
            print(json.dumps({"hosts": records, "total_hosts": total_hosts,
                              "host_offset": args.host_offset, "host_limit": args.host_limit,
                              "next_host_offset": args.host_offset + len(hosts)
                              if args.host_offset + len(hosts) < total_hosts else None},
                             ensure_ascii=False, indent=2))
        if args.action != "status":
            print(json.dumps({"output": os.path.abspath(args.output), "selected_hosts": len(hosts),
                              "total_hosts": total_hosts, "tasks_per_host_limit": args.limit}))
        return
    if args.action == "group-set":
        print(json.dumps(store.save_group(args.name, args.hosts), ensure_ascii=False, indent=2))
        return
    if args.action == "template-set":
        print(json.dumps(store.save_template(args.name, read_json(args.file)), ensure_ascii=False, indent=2))
        return
    if args.action == "batch-create":
        record = store.create_batch(args.batch_id, args.hosts or store.group(args.group),
                                    store.template(args.template), args.max_parallel, args.stagger_seconds)
        print(json.dumps(batch_summary(record), ensure_ascii=False, indent=2))
        return
    if args.action == "batch-run":
        for summary in run_batch(store, redis, args.batch_id, args.once, args.poll_seconds):
            print(json.dumps(summary, ensure_ascii=False), flush=True)
        return
    if args.action == "batch-status":
        print(json.dumps(store.batch(args.batch_id), ensure_ascii=False, indent=2))
        return
    if args.action == "batch-cancel":
        record = store.request_cancel(args.batch_id)
        try:
            record = batch_tick(store, redis, args.batch_id)
        except Exception as error:
            # The durable request remains valid if the controller is busy or Redis
            # is offline. The next batch-run cycle retries the actual cancellations.
            print(json.dumps({"cancel_requested": True, "delivery_pending": True, "reason": str(error)}))
        print(json.dumps(batch_summary(record), ensure_ascii=False, indent=2))
        return
    if not os.path.isfile(os.path.join(config["enrollments"], identifier(args.host) + ".json")):
        parser.error("Host is not enrolled")
    queue = Queue(redis, args.host)
    if args.action == "enqueue":
        task = store.template(args.template) if args.template else {}
        task["id"] = identifier(args.id) if args.id else uuid.uuid4().hex
        if args.tool is not None:
            task["tool"] = args.tool
        if args.seconds is not None:
            task["duration_seconds"] = args.seconds
        if args.threads is not None:
            task["threads"] = args.threads
        if args.memory_mb is not None:
            task["memory_mb"] = args.memory_mb
        if args.profile:
            profile = read_json(args.profile)
            if not isinstance(profile, dict) or set(profile) - {"protection", "acceptance"}:
                raise ValueError("Profile may contain only protection and acceptance")
            task.update(profile)
        validate_task(task)
        if not queue.enqueue(task):
            raise ValueError("Task ID already exists")
        print(task["id"])
    elif args.action == "cancel":
        print(json.dumps({"host": args.host, "task_id": args.task_id, "cancellation": queue.cancel(args.task_id)}))
    elif args.action == "resolve-interrupted":
        task = queue.get(args.task_id)
        if not task or task["status"] != "running":
            raise ValueError("Task is not running")
        queue.finish(task, "interrupted", {"reason": "Operator confirmed workload stopped", "verdict": "insufficient_data"})


if __name__ == "__main__":
    main()
