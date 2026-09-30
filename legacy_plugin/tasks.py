"""Bounded, atomic mutation of ONLY explicitly submitted plugin batches.

The old controller's layout is retained. Never FLUSHDB: unknown keys, alias host
mappings and claims all fail closed, and no existing batch is overwritten.
"""
import datetime
import difflib
import json
import secrets

from server_deploy.tasks import name as protocol_name, task_list

CATALOG = ['stress', 'stress-ng', 'mlc', 'mbw', 'cyclictest', 'unixbench', 'cpu2017'] + [
    'p95-{}_m{}'.format(instruction, size)
    for instruction in ('no', 'avx', 'fma3', 'avx512') for size in (1, 2, 4)]
OWNER = 'OCRUN_PLUGIN_BATCH'


def name(value):
    protocol_name(value)
    if '..' in value:
        raise ValueError('Double dots are not accepted by the node result-name protocol')
    return value

# Redis does not roll back script writes after an error. Do ALL type, bound,
# identity and schema checks before the first write, including DB0 collisions.
MAPS = """
redis.call('SELECT',0)
if redis.call('DBSIZE')>10000 then return redis.error_reply('DB0 exceeds safe inspection limit') end
local used={}; local aliases={}
for _,key in ipairs(redis.call('KEYS','*')) do
 if redis.call('TYPE',key).ok=='string' then
  local value=redis.call('GET',key)
  if string.match(value,'^%d+$') then
   used[tonumber(value)]=true
   aliases[tonumber(value)]=(aliases[tonumber(value)] or 0)+1
  end
 end
end
"""
ADD = MAPS + """
if redis.call('EXISTS',ARGV[1])~=0 then return redis.error_reply('host has a batch; inspect it first, no overwrite') end
local rows=cjson.decode(ARGV[4]); local db=nil
for n=1,tonumber(ARGV[5])-1 do
 if n~=1024 and not used[n] then
  redis.call('SELECT',n)
  if redis.call('DBSIZE')==0 then db=n; break end
 end
end
if not db then return redis.error_reply('no unused and unmapped task database') end
redis.call('SET','IDS',ARGV[2]); redis.call('SET','DATES',ARGV[3])
redis.call('SET','STATUS','pending'); redis.call('SET','CURRENT','')
redis.call('SET','OCRUN_PLUGIN_BATCH',ARGV[6])
for _,row in ipairs(rows) do
 redis.call('RPUSH','TASKS',row.name); redis.call('SET',row.name,row.runtime)
end
redis.call('SELECT',0); redis.call('SET',ARGV[1],db)
return db
"""

LOOKUP = """
local db=redis.call('GET',ARGV[1]); if not db then return false end
if not string.match(db,'^%d+$') or tonumber(db)==0 or tonumber(db)==1024 then
 return redis.error_reply('unsafe host mapping') end
redis.call('SELECT',db)
if redis.call('LLEN','TASKS')>10000 then return redis.error_reply('queue exceeds inspection limit') end
local rows={}
for _,n in ipairs(redis.call('LRANGE','TASKS',0,-1)) do
 table.insert(rows,{name=n,runtime=redis.call('GET',n)})
end
return cjson.encode({db=tonumber(db),id=redis.call('GET','IDS'),time=redis.call('GET','DATES'),
 status=redis.call('GET','STATUS'),current=redis.call('GET','CURRENT'),tasks=rows,
 owner=redis.call('GET','OCRUN_PLUGIN_BATCH'),claim=redis.call('GET','MON_PLUGIN_CLAIM')})
"""

# A cancellation only removes an UNCLAIMED, unchanged batch we created. Completed
# queues use a separate archive acknowledgement; active/failed legacy jobs remain
# under their original controller. This is intentionally not generic key deletion.
REMOVE = MAPS + """
local db=redis.call('GET',ARGV[1]); if not db then return 'absent' end
if not string.match(db,'^%d+$') or tonumber(db)==0 or tonumber(db)==1024 or aliases[tonumber(db)]~=1 then
 return redis.error_reply('unsafe or shared host mapping') end
redis.call('SELECT',db)
if redis.call('GET','IDS')~=ARGV[2] or redis.call('GET','DATES')~=ARGV[3] or
 redis.call('GET','OCRUN_PLUGIN_BATCH')~=ARGV[4] then return redis.error_reply('batch changed or is not plugin-owned') end
local owner=cjson.decode(ARGV[4]); local status=redis.call('GET','STATUS')
local actual=redis.call('LRANGE','TASKS',0,-1)
local claim=redis.call('GET','MON_PLUGIN_CLAIM')
if ARGV[5]=='cancel' then
 if status~='pending' or claim or redis.call('GET','CURRENT')~='' or #actual~=#owner.tasks then
  return redis.error_reply('batch started or changed; use node status/stop/recover') end
 for i,row in ipairs(owner.tasks) do
  if actual[i]~=row.name or redis.call('GET',row.name)~=row.runtime then return redis.error_reply('queue changed') end
 end
else
 if status~='delivered' or #actual~=0 or redis.call('GET','CURRENT')~='' then
  return redis.error_reply('batch not delivered; recover it on the node first') end
end
local allowed={IDS=true,DATES=true,STATUS=true,CURRENT=true,TASKS=true,OCRUN_PLUGIN_BATCH=true,MON_PLUGIN_CLAIM=true}
for _,row in ipairs(owner.tasks) do allowed[row.name]=true end
if redis.call('DBSIZE')>10010 then return redis.error_reply('task database too large') end
local keys=redis.call('KEYS','*')
for _,key in ipairs(keys) do
 if not allowed[key] then return redis.error_reply('unknown task database key retained: '..key) end
end
for _,key in ipairs(keys) do redis.call('DEL',key) end
redis.call('SELECT',0); redis.call('DEL',ARGV[1]); return 'removed'
"""


def parse(items):
    try:
        return task_list(items)
    except ValueError as exc:
        for item in items:
            task = item.split('=', 1)[0]
            if task not in CATALOG:
                suggestions = difflib.get_close_matches(task, CATALOG, n=2)
                if suggestions:
                    raise ValueError('{}; did you mean {}? Queue unchanged.'.format(exc, ', '.join(suggestions)))
        raise


def databases(redis):
    result = redis.call('CONFIG', 'GET', 'databases')
    if not isinstance(result, list) or len(result) != 2 or str(result[0]) != 'databases':
        raise ValueError('Cannot inspect Redis database count; no task submitted')
    count = int(result[1])
    if not 2 <= count <= 16384:
        raise ValueError('Unsupported Redis database count')
    return count


def submit(redis, host, batch, items, when=None, check=False):
    host, batch = name(host), name(batch)
    when = name(when or datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    rows = parse(items)
    count = databases(redis)
    existing = status(redis, host)
    if existing['status'] != 'no_batch':
        raise ValueError('Host already has a batch; inspect tasks status. Queue unchanged.')
    result = {'host': host, 'id': batch, 'time': when, 'tasks': rows,
              'automatic_task_polling': False, 'node_start': 'explicit', 'check': check}
    if not check:
        marker = json.dumps({'schema': 'ocrun-plugin-batch-v1', 'token': secrets.token_hex(16),
                             'tasks': rows}, sort_keys=True, separators=(',', ':'))
        result['db'] = redis.call('EVAL', ADD, 0, host, batch, when, json.dumps(rows), count, marker)
    result['status'] = 'checked' if check else 'queued'
    return result


def status(redis, host):
    data = redis.call('EVAL', LOOKUP, 0, name(host))
    if data is None:
        return {'host': host, 'status': 'no_batch'}
    result = json.loads(data)
    if result['tasks'] == {}:
        result['tasks'] = []
    result['host'] = host
    return result


def remove(redis, host, batch, when, archive=False, check=False):
    name(batch); name(when)
    value = status(redis, host)
    if value['status'] == 'no_batch':
        return {'status': 'already_absent', 'host': host, 'result_files_deleted': False}
    if value.get('id') != name(batch) or value.get('time') != name(when) or not value.get('owner'):
        raise ValueError('Explicit batch ID/time must match a plugin-owned batch')
    owner = json.loads(value['owner'])
    if owner.get('schema') != 'ocrun-plugin-batch-v1':
        raise ValueError('Unrecognized batch marker')
    parse([row['name'] + '=' + row['runtime'] for row in owner['tasks']])
    if check:
        return {'status': 'inspected', 'batch': value, 'read_only': True,
                'note': 'All guards are rechecked atomically when applied'}
    result = redis.call('EVAL', REMOVE, 0, name(host), batch, when, value['owner'], 'archive' if archive else 'cancel')
    return {'status': result, 'host': host, 'id': batch, 'time': when, 'result_files_deleted': False}
