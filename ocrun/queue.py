"""One namespace per host; atomic state transitions, no destructive dequeue."""
import json
import time
import uuid

from .common import identifier

ENQUEUE = """
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('RPUSH', KEYS[2], ARGV[1])
redis.call('ZADD', KEYS[3], ARGV[3], ARGV[1])
return 1
"""

CANCEL = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return -1 end
local task = cjson.decode(raw)
if task.status == 'pending' then
  if redis.call('LREM', KEYS[2], 1, ARGV[1]) ~= 1 then return -2 end
  task.status = 'cancelled'
  task.finished_at = tonumber(ARGV[2])
  task.result = {reason='Cancelled before execution', verdict='insufficient_data'}
  redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(task))
  return 1
end
if task.status == 'running' then
  if redis.call('GET', KEYS[3]) ~= ARGV[1] then return -3 end
  redis.call('SET', KEYS[4], ARGV[1])
  return 2
end
return 0
"""

CLAIM = """
if ARGV[3] ~= '' and redis.call('GET', KEYS[4]) ~= ARGV[3] then
  return redis.error_reply('Agent session lost')
end
if redis.call('GET', KEYS[5]) then return nil end
if redis.call('GET', KEYS[3]) then return nil end
local id = redis.call('LINDEX', KEYS[2], 0)
if not id then return nil end
local raw = redis.call('HGET', KEYS[1], id)
if not raw then return redis.error_reply('Missing task payload') end
local task = cjson.decode(raw)
if task.status ~= 'pending' then return redis.error_reply('Invalid pending state') end
task.status = 'running'
task.claim_token = ARGV[1]
task.started_at = tonumber(ARGV[2])
local updated = cjson.encode(task)
redis.call('HSET', KEYS[1], id, updated)
redis.call('SET', KEYS[3], id)
redis.call('LPOP', KEYS[2])
return updated
"""

REFRESH_SESSION = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""

RELEASE_SESSION = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""

FINISH = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return -1 end
local task = cjson.decode(raw)
if task.claim_token ~= ARGV[2] then return -2 end
if task.status ~= 'running' then
  if task.status == ARGV[3] then return 0 else return -3 end
end
if redis.call('GET', KEYS[2]) ~= ARGV[1] then return -4 end
task.status = ARGV[3]
task.finished_at = tonumber(ARGV[4])
task.result = cjson.decode(ARGV[5])
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(task))
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[3])
return 1
"""


class Queue:
    def __init__(self, redis, host):
        self.redis = redis
        self.host = identifier(host)
        self.prefix = "ocrun:h:" + host + ":"

    def key(self, name):
        return self.prefix + name

    def enqueue(self, task):
        identifier(task["id"])
        payload = dict(task, status="pending", created_at=time.time())
        return self.redis.execute("EVAL", ENQUEUE, 3, self.key("jobs"), self.key("pending"),
                                  self.key("history"), payload["id"],
                                  json.dumps(payload, allow_nan=False), payload["created_at"]) == 1

    def acquire_session(self, token, ttl):
        acquired = self.redis.execute("SET", self.key("session"), token, "NX", "EX", ttl)
        return bool(acquired) or self.redis.execute("GET", self.key("session")) == token

    def refresh_session(self, token, ttl):
        return self.redis.execute("EVAL", REFRESH_SESSION, 1, self.key("session"), token, ttl) == 1

    def release_session(self, token):
        self.redis.execute("EVAL", RELEASE_SESSION, 1, self.key("session"), token)

    def claim(self, token, session=""):
        raw = self.redis.execute("EVAL", CLAIM, 5, self.key("jobs"), self.key("pending"),
                                 self.key("running"), self.key("session"), self.key("paused"),
                                 token, time.time(), session)
        return json.loads(raw) if raw else None

    def pause(self):
        self.redis.execute("SET", self.key("paused"), "1")

    def resume(self):
        self.redis.execute("DEL", self.key("paused"))

    def is_paused(self):
        return bool(self.redis.execute("GET", self.key("paused")))

    def running(self):
        task_id = self.redis.execute("GET", self.key("running"))
        return self.get(task_id) if task_id else None

    def get(self, task_id):
        raw = self.redis.execute("HGET", self.key("jobs"), identifier(task_id))
        return json.loads(raw) if raw else None

    def finish(self, task, status, result):
        if status not in ("completed", "failed", "interrupted", "cancelled", "timed_out"):
            raise ValueError("Invalid terminal status")
        code = self.redis.execute("EVAL", FINISH, 3, self.key("jobs"), self.key("running"),
                                  self.key("cancel"), task["id"], task["claim_token"], status,
                                  time.time(), json.dumps(result, allow_nan=False))
        if code < 0:
            raise RuntimeError("Task acknowledgement rejected: " + str(code))

    def heartbeat(self, state):
        payload = dict(state, timestamp=time.time())
        self.redis.execute("SET", self.key("heartbeat"), json.dumps(payload), "EX", 120)

    def cancelled(self, task_id):
        return self.redis.execute("GET", self.key("cancel")) == task_id

    def cancel(self, task_id):
        code = self.redis.execute("EVAL", CANCEL, 4, self.key("jobs"), self.key("pending"),
                                  self.key("running"), self.key("cancel"),
                                  identifier(task_id), time.time())
        if code < 0:
            raise ValueError("Cancellation rejected: " + str(code))
        return {0: "already_terminal", 1: "cancelled", 2: "requested"}[code]

    def task_page(self, offset=0, limit=50, status=None):
        """Newest-first bounded reads. A filter applies to this page, not the whole history."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("History offset must be nonnegative; limit must be 1..200")
        if status is not None and status not in ("pending", "running", "completed", "failed",
                                               "cancelled", "interrupted", "timed_out"):
            raise ValueError("Invalid task status filter")
        ids = self.redis.execute("ZREVRANGE", self.key("history"), offset, offset + limit - 1)
        total = self.redis.execute("ZCARD", self.key("history"))
        records = [self.get(task_id) for task_id in ids]
        records = [task for task in records if task and (status is None or task["status"] == status)]
        next_offset = offset + len(ids)
        return {"tasks": records, "offset": offset, "limit": limit, "indexed_total": total,
                "next_offset": next_offset if ids and next_offset < total else None,
                "history_incomplete": total != self.redis.execute("HLEN", self.key("jobs")),
                "filter_applies_to_page": status is not None}

    def tasks(self, limit=50):
        return self.task_page(limit=limit)["tasks"]

    def reindex_history(self):
        """Explicit upgrade migration; normal status never scans the entire job hash."""
        cursor, indexed = 0, 0
        while True:
            cursor, pairs = self.redis.execute("HSCAN", self.key("jobs"), cursor, "COUNT", 100)
            # redis-py test adapters decode hashes; the runtime RESP client returns a flat list.
            values = list(pairs.values()) if isinstance(pairs, dict) else pairs[1::2]
            for raw in values:
                task = json.loads(raw)
                self.redis.execute("ZADD", self.key("history"), task.get("created_at", 0),
                                   identifier(task["id"]))
                indexed += 1
            if int(cursor) == 0:
                return indexed
