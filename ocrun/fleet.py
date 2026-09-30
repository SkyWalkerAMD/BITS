"""Center-side durable batch dispatch. Clients retain their isolated Redis namespace."""
import contextlib
import copy
import glob
import math
import os
import time
import uuid

from .common import exclusive_lock, identifier, read_json, write_json
from .queue import Queue
from .workloads import validate_task

TERMINAL = {"completed", "failed", "cancelled", "interrupted", "timed_out"}
ACTIVE = {"submitting", "pending", "running"}
TASK_FIELDS = {"tool", "duration_seconds", "threads", "memory_mb", "protection", "acceptance"}


def task_template(value):
    if not isinstance(value, dict) or set(value) - TASK_FIELDS:
        raise ValueError("Template may contain only workload parameters, protection and acceptance")
    value = copy.deepcopy(value)
    validate_task(value)
    return value


class FleetStore:
    def __init__(self, config):
        self.config = config
        self.root = os.path.join(config["data_root"], "fleet")

    def path(self, kind, name):
        return os.path.join(self.root, kind, identifier(name) + ".json")

    def enrolled_hosts(self):
        return [identifier(os.path.basename(path)[:-5]) for path in
                sorted(glob.glob(os.path.join(self.config["enrollments"], "*.json")))]

    def check_hosts(self, hosts):
        known = set(self.enrolled_hosts())
        result = sorted(set(identifier(host) for host in hosts))
        if not result or len(result) > 10000 or set(result) - known:
            raise ValueError("Select 1..10000 enrolled hosts")
        return result

    def save_group(self, name, hosts):
        record = {"name": identifier(name), "hosts": self.check_hosts(hosts)}
        write_json(self.path("groups", name), record)
        return record

    def group(self, name):
        return self.check_hosts(read_json(self.path("groups", name))["hosts"])

    def save_template(self, name, value):
        record = {"name": identifier(name), "task": task_template(value)}
        write_json(self.path("templates", name), record)
        return record

    def template(self, name):
        return task_template(read_json(self.path("templates", name))["task"])

    @contextlib.contextmanager
    def locked_batch(self, batch_id):
        path = self.path("batches", batch_id)
        with exclusive_lock(path + ".lock"):
            yield path

    def create_batch(self, batch_id, hosts, template, max_parallel=1, stagger_seconds=0):
        identifier(batch_id)
        hosts = self.check_hosts(hosts)
        task = task_template(template)
        if type(max_parallel) is not int or not 1 <= max_parallel <= 10000:
            raise ValueError("max_parallel must be 1..10000")
        if (type(stagger_seconds) not in (int, float) or not math.isfinite(stagger_seconds) or
                not 0 <= stagger_seconds <= 86400):
            raise ValueError("stagger_seconds must be 0..86400")
        # Save immutable task IDs before any network operation. Lost enqueue responses
        # and a killed controller can therefore never produce duplicate workloads.
        record = {"id": batch_id, "created_at": time.time(), "status": "waiting", "task": task,
                  "max_parallel": max_parallel, "stagger_seconds": stagger_seconds,
                  "last_dispatch_at": None, "cancel_requested": False,
                  "members": [{"host": host, "task_id": uuid.uuid5(uuid.NAMESPACE_URL,
                                "ocrun:" + batch_id + ":" + host).hex, "state": "waiting"}
                              for host in hosts]}
        with self.locked_batch(batch_id) as path:
            if os.path.exists(path):
                raise ValueError("Batch ID already exists; use batch-run to resume it")
            write_json(path, record)
        return record

    def batch(self, batch_id):
        path = self.path("batches", batch_id)
        record = read_json(path)
        if os.path.exists(path + ".cancel"):
            record["cancel_requested"] = True
        return record

    def request_cancel(self, batch_id):
        path = self.path("batches", batch_id)
        read_json(path)
        # A separate durable marker can be written even while a controller holds
        # the plan lock or is waiting on a slow Redis response.
        write_json(path + ".cancel", {"id": batch_id, "requested_at": time.time()})
        return self.batch(batch_id)


def _verify_owned_task(record, member, task):
    if task.get("batch_id") != record["id"] or any(task.get(key) != value
                                                     for key, value in record["task"].items()):
        raise RuntimeError("Task ID collision on " + member["host"] + "; batch dispatch stopped")


def _dispatch(record, member, queue, path, now):
    # Reserve a slot on durable storage first. A crash at every subsequent point
    # is recoverable by looking up this exact task ID in Redis.
    member["state"] = "submitting"
    record["last_dispatch_at"] = now
    write_json(path, record)
    task = dict(record["task"], id=member["task_id"], batch_id=record["id"])
    if not queue.enqueue(task):
        existing = queue.get(task["id"])
        if existing is None:
            raise RuntimeError("Submission was rejected without a task record")
        _verify_owned_task(record, member, existing)
        member["state"] = existing["status"]
    else:
        member["state"] = "pending"
    if os.path.exists(path + ".cancel"):
        # A cancellation can arrive during the network call, after the controller's
        # pre-dispatch check. Cancel that in-flight submission as soon as its ID is known.
        record["cancel_requested"] = True
        record["status"] = "cancelling"
        outcome = queue.cancel(member["task_id"])
        if outcome == "requested":
            member["cancellation_requested"] = True
        else:
            member["state"] = queue.get(member["task_id"])["status"]
    write_json(path, record)


def batch_tick(store, redis, batch_id, now=None):
    """Perform one resumable dispatch cycle under an exclusive plan lock.

    Cancellation requests use an independent durable marker so a slow network
    operation cannot prevent the operator from recording a stop request.

    The cap includes this batch's queued/running/submitting tasks. A task on an
    offline host holds its slot until its actual terminal result is acknowledged.
    This cap does not include separately submitted manual work or other batches.
    """
    now = time.time() if now is None else now
    with store.locked_batch(batch_id) as path:
        record = store.batch(batch_id)
        if record["status"] in ("finished", "cancelled"):
            return record
        record["status"] = "cancelling" if record["cancel_requested"] else "running"
        queues = {}
        for member in record["members"]:
            if member["state"] in TERMINAL:
                continue
            queue = queues.setdefault(member["host"], Queue(redis, member["host"]))
            task = queue.get(member["task_id"])
            if task:
                _verify_owned_task(record, member, task)
                member["state"] = task["status"]
            elif member["state"] in ("pending", "running"):
                # A restored/emptied Redis must never silently restart hardware work.
                raise RuntimeError("Previously submitted task missing on " + member["host"] +
                                   "; restore Redis or resolve the batch manually")
            if record["cancel_requested"] and member["state"] not in TERMINAL:
                if task:
                    outcome = queue.cancel(member["task_id"])
                    if outcome in ("cancelled", "already_terminal"):
                        member["state"] = queue.get(member["task_id"])["status"]
                    else:
                        member["cancellation_requested"] = True
                else:
                    member["state"] = "cancelled"
        write_json(path, record)

        if not record["cancel_requested"]:
            active = sum(member["state"] in ACTIVE for member in record["members"])
            for member in record["members"]:
                if os.path.exists(path + ".cancel"):
                    record["cancel_requested"] = True
                    record["status"] = "cancelling"
                    break
                queue = queues.get(member["host"])
                if member["state"] == "submitting":
                    # The slot was already reserved before an uncertain enqueue.
                    _dispatch(record, member, queue, path, now)
                elif member["state"] == "waiting" and active < record["max_parallel"]:
                    previous = record["last_dispatch_at"]
                    if previous is not None and now - previous < record["stagger_seconds"]:
                        break
                    if (queue.is_paused() or not redis.execute("GET", queue.key("heartbeat")) or
                            redis.execute("GET", queue.key("running")) or
                            redis.execute("LLEN", queue.key("pending"))):
                        continue
                    _dispatch(record, member, queue, path, now)
                    active += 1
        if all(member["state"] in TERMINAL for member in record["members"]):
            record["status"] = "cancelled" if record["cancel_requested"] else "finished"
            record["finished_at"] = now
        record["updated_at"] = now
        write_json(path, record)
        return record


def batch_summary(record):
    counts = {}
    for member in record["members"]:
        counts[member["state"]] = counts.get(member["state"], 0) + 1
    return {"id": record["id"], "status": record["status"], "counts": counts,
            "max_parallel": record["max_parallel"], "stagger_seconds": record["stagger_seconds"],
            "cancel_requested": record["cancel_requested"]}


def run_batch(store, redis, batch_id, once=False, poll_seconds=5):
    if type(poll_seconds) not in (int, float) or not 1 <= poll_seconds <= 60:
        raise ValueError("Controller poll interval must be 1..60 seconds")
    # Only one foreground controller for this installation. Cancellation markers
    # can still be saved while this lock is held.
    with exclusive_lock(os.path.join(store.root, "controller.lock")):
        while True:
            record = batch_tick(store, redis, batch_id)
            yield batch_summary(record)
            if once or record["status"] in ("finished", "cancelled"):
                break
            time.sleep(poll_seconds)
