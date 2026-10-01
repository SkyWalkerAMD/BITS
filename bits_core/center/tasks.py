"""Control tasks using the existing DB0 host mapping and per-host Redis keys."""
import datetime
import json
import re


NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
WORKLOAD = re.compile(r"(?:stress(?:-ng)?(?:_r2)?|mlc|mbw|cyclictest|unixbench|cpu2017|cpu|p95-(?:no|avx|fma3|avx512)_m[124])\Z")


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError("Use 1-128 letters/digits/underscore/dot/hyphen, beginning with a letter or digit")
    return value


def task_list(items):
    if not 1 <= len(items) <= 10000:
        raise ValueError("A batch requires 1-10000 task entries")
    result, durations = [], {}
    for item in items:
        parts = item.split("=", 1)
        if len(parts) != 2 or not WORKLOAD.fullmatch(parts[0]):
            raise ValueError("Unknown task {!r}; use e.g. stress=60 or stress-ng=60".format(item))
        task, seconds = parts
        if not seconds.isdigit() or not 1 <= int(seconds) <= 31 * 86400:
            raise ValueError("Task duration must be 1-2678400 seconds")
        seconds = str(int(seconds))
        if task in durations and durations[task] != seconds:
            raise ValueError("The original protocol stores one duration per task name; repeated names must use the same duration")
        durations[task] = seconds
        result.append({"name": task, "runtime": seconds})
    return result


ADD = """
if redis.call('EXISTS',ARGV[1])~=0 then return redis.error_reply('host already has a batch; inspect/delete it explicitly') end
local items=cjson.decode(ARGV[4])
local db=nil
for n=1,tonumber(ARGV[5])-1 do
  if n~=1024 then
    redis.call('SELECT',n)
    if redis.call('DBSIZE')==0 then db=n; break end
  end
end
if not db then return redis.error_reply('no free host database') end
redis.call('SET','IDS',ARGV[2]); redis.call('SET','DATES',ARGV[3])
redis.call('SET','STATUS','pending'); redis.call('SET','CURRENT','')
for _,item in ipairs(items) do
  redis.call('RPUSH','TASKS',item.name); redis.call('SET',item.name,item.runtime)
end
redis.call('SELECT',0); redis.call('SET',ARGV[1],db)
return db
"""

STATUS = """
local db=redis.call('GET',ARGV[1]); if not db then return false end
if not string.match(db,'^%d+$') or tonumber(db)==0 then return redis.error_reply('invalid host mapping') end
redis.call('SELECT',db)
local tasks={}
for _,n in ipairs(redis.call('LRANGE','TASKS',0,9999)) do table.insert(tasks,{name=n,runtime=redis.call('GET',n)}) end
return cjson.encode({db=tonumber(db),id=redis.call('GET','IDS'),time=redis.call('GET','DATES'),
 status=redis.call('GET','STATUS'),current=redis.call('GET','CURRENT'),tasks=tasks})
"""

DELETE = """
local db=redis.call('GET',ARGV[1]); if not db then return 'absent' end
if not string.match(db,'^%d+$') or tonumber(db)==0 then return redis.error_reply('invalid host mapping') end
redis.call('SELECT',db)
if redis.call('GET','IDS')~=ARGV[2] then return redis.error_reply('batch identity changed') end
local status=redis.call('GET','STATUS')
if status~='pending' and status~='delivered' then return redis.error_reply('batch is active or requires recovery; stop/recover it on the node first') end
redis.call('FLUSHDB'); redis.call('SELECT',0); redis.call('DEL',ARGV[1]); return 'deleted'
"""


def add(redis, host, batch, when, items, databases=2048):
    tasks = task_list(items)
    when = when or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    db = redis.call("EVAL", ADD, 0, name(host), name(batch), name(when), json.dumps(tasks), databases)
    return {"host": host, "db": db, "id": batch, "time": when, "tasks": tasks,
            "status": "queued", "node_start": "explicit", "automatic_task_polling": False}


def status(redis, host):
    data = redis.call("EVAL", STATUS, 0, name(host))
    if data is None:
        return {"host": host, "status": "no_batch"}
    value = json.loads(data)
    if value["tasks"] == {}:
        value["tasks"] = []
    value["host"] = host
    return value


def delete(redis, host, batch):
    return {"host": host, "id": batch, "status": redis.call("EVAL", DELETE, 0, name(host), name(batch)),
            "result_files_deleted": False}
