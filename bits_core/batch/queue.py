"""Atomic access to the original per-host Redis layout; no idle polling."""
import json
from pathlib import Path
import re
import shutil

from common import run, read, regular

from bits_layout import LAYOUT
CONNECTION = Path(LAYOUT.connection)


def credentials(host, port, node):
    if not CONNECTION.exists() and not CONNECTION.is_symlink():
        return None
    regular(CONNECTION.lstat(), CONNECTION, private=True)
    value = json.loads(read(CONNECTION, 4096).decode('utf-8'))
    if (value.get('schema') != 'ocrun-node-connection-v1' or value.get('rdb_server') != host or
            value.get('rdb_port') != port or value.get('node') != node or
            not isinstance(value.get('password'), str) or not re.fullmatch(r'[0-9a-f]{64}', value['password'])):
        raise ValueError('Node connection identity or credentials differ; inspect ' + str(CONNECTION))
    return value['password']

SNAPSHOT = """
local n=redis.call('LLEN','TASKS')
if n>10000 then return redis.error_reply('too many tasks') end
local names=redis.call('LRANGE','TASKS',0,-1)
local tasks={}
for i,name in ipairs(names) do
  tasks[i]={name=name,runtime=redis.call('GET',name)}
end
return cjson.encode({id=redis.call('GET','IDS'),time=redis.call('GET','DATES'),tasks=tasks})
"""

# Check every remaining entry and duration in the same atomic operation as LPOP.
# Keep duration keys: the old layout has one duration per NAME, including repeats.
CLAIM = """
if redis.call('GET','IDS')~=ARGV[1] or redis.call('GET','DATES')~=ARGV[2] then
  return redis.error_reply('batch changed') end
local expected=cjson.decode(ARGV[3])
local actual=redis.call('LRANGE','TASKS',0,-1)
if #actual~=#expected then return redis.error_reply('queue changed') end
for i,item in ipairs(expected) do
  if actual[i]~=item.name or redis.call('GET',item.name)~=item.runtime then
    return redis.error_reply('task changed') end
end
if #expected==0 then return redis.error_reply('empty queue') end
redis.call('LPOP','TASKS')
redis.call('SET','CURRENT',expected[1].name)
redis.call('SET','STATUS','running')
redis.call('SET','MON_PLUGIN_CLAIM',ARGV[4])
return 'claimed'
"""

UPDATE = """
if redis.call('GET','IDS')~=ARGV[1] or redis.call('GET','DATES')~=ARGV[2]
or redis.call('GET','MON_PLUGIN_CLAIM')~=ARGV[3] then return 'changed' end
redis.call('SET','STATUS',ARGV[4])
redis.call('SET','CURRENT',ARGV[5])
return 'updated'
"""


class Queue:
    def __init__(self, host, node, port=6379):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', host):
            raise ValueError('Invalid Redis host')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', node):
            raise ValueError('Invalid node name')
        if not 1 <= port <= 65535:
            raise ValueError('Invalid Redis port')
        executable = shutil.which('redis-cli', path='/usr/bin:/bin:/usr/local/bin')
        if not executable:
            executable = shutil.which('valkey-cli', path='/usr/bin:/bin:/usr/local/bin')
        if not executable:
            raise ValueError('redis-cli is required')
        self.base = [executable, '--raw', '-h', host, '-p', str(port)]
        self.node = node
        self.password = credentials(host, port, node)

    def call(self, db, *args):
        argv = self.base + ['-n', str(db)] + list(args)
        data = (run(argv, 12, redis_password=self.password) if self.password is not None else run(argv, 12)).decode('utf-8').strip()
        # Old redis-cli may return zero even for Redis protocol errors.
        if data.startswith(('ERR ', 'WRONGTYPE ', 'NOAUTH ', 'NOPERM ', '(error)')):
            raise ValueError('Redis rejected the operation: ' + data[:180])
        return data

    def mapping(self):
        value = self.call(0, 'GET', self.node)
        if value == '':
            return None
        if not re.fullmatch(r'[0-9]{1,6}', value) or int(value) == 0:
            raise ValueError('Invalid host database mapping')
        return int(value)

    def snapshot(self):
        db = self.mapping()
        if db is None:
            return None
        value = json.loads(self.call(db, 'EVAL', SNAPSHOT, '0'))
        if self.mapping() != db:
            raise ValueError('Host database changed during task lookup')
        # Redis cjson encodes an empty Lua array as {}.
        if value['tasks'] == {}:
            value['tasks'] = []
        value['db'] = db
        return value

    def claim(self, snapshot, remaining, token):
        if self.mapping() != snapshot['db']:
            raise ValueError('Host database changed before task claim')
        result = self.call(snapshot['db'], 'EVAL', CLAIM, '0', snapshot['id'], snapshot['time'],
                           json.dumps(remaining, separators=(',', ':')), token)
        if result != 'claimed':
            raise ValueError('Task claim was not confirmed; inspect the local claim journal')
        if self.mapping() != snapshot['db']:
            raise ValueError('Host mapping changed after claim; workload not started, inspect claim journal')

    def update(self, snapshot, token, status, current=''):
        if self.mapping() != snapshot['db']:
            return False
        return self.call(snapshot['db'], 'EVAL', UPDATE, '0', snapshot['id'], snapshot['time'],
                         token, status, current) == 'updated'
