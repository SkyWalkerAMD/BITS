"""Bounded, offline internal acceptance report; never infer hardware qualification."""
import datetime
import hashlib
import html
import json
import math
import os
from pathlib import Path
import platform
import re

SCHEMA = 'ocrun-acceptance-report-v1'
METRICS = (('load1', '系统负载', '', 4), ('frequency_mhz', '核心报告频率均值', 'MHz', 5),
           ('cpu_temp_c', 'CPU最高温度', '°C', 6), ('vrm_temp_c', 'VRM温度', '°C', 7),
           ('cpu_power_w', 'CPU封装功耗合计', 'W', 8), ('system_power_w', '整机功耗', 'W', 9))
MAX_GROUPS = 128
MAX_CORES = 4096
MAX_REPORT_BYTES = 16 * 1024 ** 2


def utc():
    return datetime.datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def executable_hash(binary):
    # System interpreters may legitimately be linked; use the same executable
    # trust policy as the guardian, never relax writable result-file checks.
    from security import trusted_executable
    with trusted_executable(binary) as (fd, pinned):
        before = os.fstat(fd)
        digest = hashlib.sha256()
        with open(pinned, 'rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Executable changed during evidence capture')
        return digest.hexdigest()


def machine_snapshot():
    """Only a bounded allowlist of local OS facts; no commands, keys or activation files."""
    warnings = []
    def text(path, limit=16384):
        try:
            with open(path, 'rb') as stream:
                value = stream.read(limit + 1)
            if len(value) > limit:
                raise ValueError('size limit')
            return value.decode('utf-8', 'replace').strip()
        except (OSError, ValueError):
            warnings.append('未获取: ' + path)
            return None
    release = text('/etc/os-release') or ''
    os_name = next((v.split('=', 1)[1].strip('"') for v in release.splitlines()
                    if v.startswith('PRETTY_NAME=')), None)
    cpu_text = text('/proc/cpuinfo', 4 * 1024 ** 2) or ''
    processors = []
    for block in cpu_text.split('\n\n'):
        record = dict((line.split(':', 1)[0].strip(), line.split(':', 1)[1].strip())
                      for line in block.splitlines() if ':' in line)
        if 'processor' in record:
            processors.append(record)
    sockets = {r['physical id'] for r in processors if 'physical id' in r}
    cores = {(r['physical id'], r['core id']) for r in processors if 'physical id' in r and 'core id' in r}
    mem = text('/proc/meminfo') or ''
    match = re.search(r'^MemTotal:\s+(\d+) kB$', mem, re.M)
    dmi = {name: text('/sys/class/dmi/id/' + name, 1024) for name in
           ('sys_vendor', 'product_name', 'product_serial', 'board_name', 'bios_version', 'bios_date')}
    disks = []
    try:
        devices = [d for d in sorted(Path('/sys/block').iterdir())
                   if re.fullmatch(r'(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)', d.name)]
        if len(devices) > 128:
            warnings.append('块设备超过128项，配置快照仅列前128项')
        for device in devices[:128]:
            size = text(str(device / 'size'), 64)
            disks.append({'name': device.name, 'model': text(str(device / 'device/model'), 256),
                          'bytes': int(size) * 512 if size and size.isdigit() else None})
    except OSError:
        warnings.append('未获取块设备列表')
    return {'captured_at': utc(), 'capture_phase': 'batch_start', 'hostname': platform.node(),
            'os': os_name, 'kernel': platform.release(), 'architecture': platform.machine(),
            'cpu_models': sorted({r.get('model name', r.get('Processor', '未知')) for r in processors}),
            'logical_cpus': len(processors) or None, 'physical_cores': len(cores) or None,
            'sockets': len(sockets) or None, 'allowed_logical_cpus': len(os.sched_getaffinity(0)),
            'memory_bytes': int(match.group(1)) * 1024 if match else None, 'dmi': dmi, 'disks': disks,
            'dimm_details': '未采集内存条型号、槽位与频率；总内存来自操作系统可见容量',
            'warnings': warnings[:128], 'source': 'local_proc_sys_and_os_release',
            'hardware_identity_independently_verified': False}


class Metric:
    def __init__(self):
        self.count = self.missing = self.zeros = 0
        self.minimum = self.maximum = self.mean = None

    def add(self, value):
        if value is None or value == '':
            self.missing += 1
            return
        value = float(value)
        if not math.isfinite(value):
            raise ValueError('Non-finite report metric')
        self.count += 1
        self.zeros += value == 0
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.mean = value if self.mean is None else self.mean + (value - self.mean) / self.count

    def value(self):
        return {'count': self.count, 'missing': self.missing, 'zero_count': self.zeros,
                'min': self.minimum, 'mean': self.mean, 'max': self.maximum}


class Statistics:
    def __init__(self):
        self.metrics = {name: Metric() for name, unused, unit, column in METRICS}
        self.groups, self.cores, self.sockets = {}, {}, {}
        self.statuses, self.schemas, self.versions = {}, {}, set()
        self.rows = self.gaps = self.clock_reversals = 0
        self.first_uptime = self.last_uptime = self.max_gap = None
        self.trend, self.bucket_size = [], 1

    def consume(self, row, detail):
        self.rows += 1
        group = row[2]
        if group not in self.groups:
            if len(self.groups) >= MAX_GROUPS:
                raise ValueError('Detailed report exceeds 128 distinct monitoring task labels')
            self.groups[group] = {name: Metric() for name in self.metrics}
        for name, unused, unit, column in METRICS:
            self.metrics[name].add(row[column])
            self.groups[group][name].add(row[column])
        provider = detail['provider']
        status = provider['status']
        self.statuses[status] = self.statuses.get(status, 0) + 1
        uptime = float(row[3]) if row[3] else None
        if uptime is not None:
            if self.last_uptime is not None:
                gap = uptime - self.last_uptime
                self.max_gap = gap if self.max_gap is None else max(self.max_gap, gap)
                self.gaps += gap > 6  # Explicit observation threshold, not a hardware alarm.
                self.clock_reversals += gap < 0
            if self.first_uptime is None:
                self.first_uptime = uptime
            self.last_uptime = uptime
        # Hierarchical buckets retain every sample's contribution, at most 240
        # plot points. This is an averaged trend, never a peak/percentile claim.
        if not self.trend or self.trend[-1]['rows'] == self.bucket_size:
            self.trend.append({'rows': 0, 'time': row[1], 'metrics': {}})
        bucket = self.trend[-1]
        bucket['rows'] += 1
        for name, column in (('cpu_temp_c', 6), ('cpu_power_w', 8), ('frequency_mhz', 5)):
            total, n = bucket['metrics'].get(name, (0., 0))
            if row[column]:
                total += float(row[column])
                n += 1
            bucket['metrics'][name] = [total, n]
        if len(self.trend) > 240:
            merged = []
            for i in range(0, len(self.trend), 2):
                a = self.trend[i]
                if i + 1 < len(self.trend):
                    b = self.trend[i + 1]
                    a = {'rows': a['rows'] + b['rows'], 'time': a['time'], 'metrics': {
                        name: [a['metrics'][name][0] + b['metrics'][name][0],
                               a['metrics'][name][1] + b['metrics'][name][1]] for name in a['metrics']}}
                merged.append(a)
            self.trend = merged
            self.bucket_size *= 2
        data = provider.get('data')
        if status != 'ok' or not data:
            return
        schema = data['schema']
        self.schemas[schema] = self.schemas.get(schema, 0) + 1
        version = str(data.get('version', 'unknown'))[:80]
        if version not in self.versions and len(self.versions) >= 32:
            raise ValueError('Too many provider versions in one report')
        self.versions.add(version)
        v1 = schema == 'sckocp-mon-v1'
        for kind, key, fields in (('cores', 'cpu', ('mhz', 'temp_c', 'c0_pct')),
                                  ('sockets', 'id', ('temp_max_c', 'pkg_w', 'core_mhz'))):
            target = self.cores if kind == 'cores' else self.sockets
            for record in data.get(kind, []):
                identifier = str(record.get(key, 'unknown'))
                if identifier not in target:
                    if len(target) >= (MAX_CORES if kind == 'cores' else 128):
                        raise ValueError('Detailed report topology limit exceeded')
                    target[identifier] = {f: Metric() for f in fields}
                for field in fields:
                    mapping = {'mhz': 'active_mhz', 'temp_c': 'temperature_c', 'temp_max_c': 'temperature_c',
                               'pkg_w': 'package_watts', 'core_mhz': 'active_mhz', 'c0_pct': 'c0_percent'}
                    metric = record.get('metrics', {}).get(mapping.get(field, field), {}) if not v1 else {}
                    value = record.get(field) if v1 else metric.get('value') if metric.get('status') == 'ok' else None
                    target[identifier][field].add(value)

    def value(self):
        topology = lambda values: {key: {name: metric.value() for name, metric in fields.items()}
                                   for key, fields in sorted(values.items())}
        return {'rows': self.rows, 'metrics': {name: value.value() for name, value in self.metrics.items()},
                'by_task_label': topology(self.groups), 'cores': topology(self.cores), 'sockets': topology(self.sockets),
                'provider_status_counts': self.statuses, 'provider_schema_counts': self.schemas,
                'provider_versions': sorted(self.versions), 'gap_observation_threshold_s': 6,
                'gaps_above_threshold': self.gaps, 'uptime_reversals': self.clock_reversals,
                'max_gap_s': self.max_gap, 'first_uptime_s': self.first_uptime, 'last_uptime_s': self.last_uptime,
                'trend': [{'time': b['time'], 'rows': b['rows'], 'metrics': {
                    name: total / n if n else None for name, (total, n) in b['metrics'].items()}} for b in self.trend],
                'trend_definition': 'consecutive_sample_bucket_mean; no percentile or energy integration',
                'task_grouping': 'monitoring label; repeated steps with identical labels are combined'}


def make_record(state, statistics):
    metrics = statistics.value()
    issues = []
    execution = state.get('execution_result', 'legacy_launch_status_only')
    if execution != 'completed':
        issues.append('任务执行结果为 {}，不能视为完整通过。'.format(execution))
    if state.get('outcome') not in (None, 'scheduler_finished'):
        issues.append('收尾原因: ' + str(state['outcome']))
    if state.get('execution_error'):
        issues.append(str(state['execution_error'])[:512])
    unavailable = sum(n for status, n in metrics['provider_status_counts'].items() if status != 'ok')
    if unavailable:
        issues.append('{} 条采样的监控来源不可用；不可用读数未补零。'.format(unavailable))
    if metrics['gaps_above_threshold'] or metrics['uptime_reversals']:
        issues.append('观察到 {} 次采样间隔超过6秒、{} 次运行时长倒退；需要核查采集连续性。'.format(
            metrics['gaps_above_threshold'], metrics['uptime_reversals']))
    machine = state.get('machine_snapshot', {'capture_phase': 'not_recorded_at_batch_start',
                                            'warnings': ['旧批次未记录机器配置，不用当前机器信息冒充历史配置。']})
    issues += machine.get('warnings', [])
    for index, step in enumerate(state.get('steps', []), 1):
        if step.get('execution') not in ('duration_reached', 'finished') or step.get('cleanup_confirmed') is not True:
            issues.append('步骤 {} 的执行或进程清理缺少成功依据，详见任务表。'.format(index))
    sources = {name: meta for name, meta in state['artifacts'].items()
               if name.endswith(('.mon', '.mon.sckocp.jsonl', '.xlsx'))}
    return {'schema': SCHEMA, 'case': state['case'], 'task_id': state['task_id'], 'task_time': state['task_time'],
            'machine_key': state.get('remote', '').rsplit('/', 1)[-1], 'machine': machine,
            'generated_from_sealed_at': state['sealed_at'], 'batch_created_at': state['created_at'],
            'finalizer_version': state['version'], 'execution_result': execution,
            'hardware_acceptance': 'not_automatically_assessed', 'acceptance_thresholds': None,
            'data_quality': state.get('data_quality', 'unknown'), 'statistics': metrics,
            'steps': state.get('steps', []), 'source_artifacts': sources, 'issues': issues,
            'delivery': 'This document precedes upload. Verify all delivered files against the separate .finish.json receipt.',
            'scope': 'internal acceptance evidence; not a hardware qualification certificate',
            'time_basis': 'step timestamps UTC; MON times node-local without offset; gaps use monotonic system uptime'}


def esc(value):
    return html.escape('未知 / 未提供' if value is None else str(value), quote=True)


def number(value):
    return '—' if value is None else '{:.2f}'.format(value)


def table(headers, rows):
    return '<div class="table-wrap"><table><thead><tr>' + ''.join('<th>' + esc(h) + '</th>' for h in headers) + \
        '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + esc(c) + '</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def chart(trend, key, title, unit):
    points = [(i, b['metrics'].get(key)) for i, b in enumerate(trend)]
    valid = [v for i, v in points if v is not None]
    if not valid:
        return '<div class="chart"><h3>' + esc(title) + '</h3><p>无可用读数</p></div>'
    low, high = min(valid), max(valid)
    padding = max(1., abs(high) * .005) if high == low else max((high - low) * .05, .01)
    low, high = low - padding, high + padding
    span = high - low
    pieces, current = [], []
    for i, value in points:
        if value is None:
            if current:
                pieces.append(current)
                current = []
        else:
            current.append('{:.1f},{:.1f}'.format(45 + i * 530 / max(1, len(points) - 1), 150 - (value - low) * 112 / span))
    if current:
        pieces.append(current)
    lines = ''.join('<polyline points="' + ' '.join(p) + '" fill="none" stroke="#087f8c" stroke-width="2.4"/>' for p in pieces)
    lines += ''.join('<circle cx="' + p[0].split(',')[0] + '" cy="' + p[0].split(',')[1] + '" r="3" fill="#087f8c"/>'
                     for p in pieces if len(p) == 1)
    return ('<div class="chart"><h3>' + esc(title) + ' <small>' + esc(unit) + '</small></h3>'
            '<svg viewBox="0 0 620 180" role="img" aria-label="' + esc(title) + '分桶均值趋势">'
            '<path d="M45 25V150H580" stroke="#b6c6cb" fill="none"/>'
            '<text x="4" y="34">' + esc(number(high)) + '</text><text x="4" y="150">' + esc(number(low)) + '</text>' + lines +
            '<text x="45" y="174">起始样本</text><text x="510" y="174">结束样本</text></svg></div>')


def render(record):
    machine, stats = record['machine'], record['statistics']
    dmi = machine.get('dmi', {})
    execution_labels = {'completed': '计划执行完成', 'early_exit': '提前退出', 'interrupted': '异常中断',
                        'legacy_launch_status_only': '旧记录：缺少执行证明'}
    execution = execution_labels.get(record['execution_result'], record['execution_result'])
    configuration = [('主机 / 资产标识', record['machine_key']), ('系统型号', dmi.get('product_name')),
                     ('厂商 / 主板', '{} / {}'.format(dmi.get('sys_vendor'), dmi.get('board_name'))),
                     ('机器序列号（DMI）', dmi.get('product_serial')), ('BIOS版本 / 日期', '{} / {}'.format(dmi.get('bios_version'), dmi.get('bios_date'))),
                     ('操作系统', machine.get('os')), ('内核 / 架构', '{} / {}'.format(machine.get('kernel'), machine.get('architecture'))),
                     ('CPU型号', ' / '.join(machine.get('cpu_models', [])) or None),
                     ('插槽 / 物理核 / 逻辑CPU', '{} / {} / {}'.format(machine.get('sockets'), machine.get('physical_cores'), machine.get('logical_cpus'))),
                     ('任务可用逻辑CPU', machine.get('allowed_logical_cpus')),
                     ('OS可见内存 GiB', number(machine['memory_bytes'] / 1024 ** 3) if machine.get('memory_bytes') is not None else None),
                     ('配置采集时间 UTC', machine.get('captured_at')), ('配置来源', machine.get('source'))]
    steps = []
    for i, step in enumerate(record['steps'], 1):
        steps.append([i, step['name'], step.get('runtime_s'), number(step.get('elapsed_s')),
                      step.get('started_at'), step.get('ended_at'), step.get('execution'),
                      step.get('exit_code'), step.get('cleanup_confirmed'), step.get('tool_version', '未知')])
    metric_rows = []
    for name, title, unit, unused in METRICS:
        item = stats['metrics'][name]
        metric_rows.append([title, unit, item['count'], item['missing'], item['zero_count'],
                            number(item['min']), number(item['mean']), number(item['max'])])
    by_task = []
    for label, metrics in stats['by_task_label'].items():
        by_task.append([label, metrics['load1']['count'], number(metrics['cpu_temp_c']['max']),
                        number(metrics['cpu_power_w']['mean']), number(metrics['cpu_power_w']['max']),
                        number(metrics['frequency_mhz']['mean'])])
    cores = [[identifier, number(m['mhz']['mean']), number(m['temp_c']['max']), number(m['c0_pct']['mean'])]
             for identifier, m in stats['cores'].items()]
    sockets = [[identifier, number(m['temp_max_c']['max']), number(m['pkg_w']['mean']), number(m['pkg_w']['max'])]
               for identifier, m in stats['sockets'].items()]
    binaries = [[i, s['name'], s.get('binary'), s.get('binary_sha256'), s.get('tool_version', '未知')]
                for i, s in enumerate(record['steps'], 1)]
    files = [[name, item['bytes'], item['sha256']] for name, item in sorted(record['source_artifacts'].items())]
    warnings = ''.join('<li>' + esc(v) + '</li>' for v in record['issues']) or '<li>未从本次记录中发现上述流程异常；不代表已排除硬件错误。</li>'
    metadata = [('批次ID', record['case']), ('任务编号', record['task_id']), ('任务时间标识', record['task_time']),
                ('开始 UTC', record['batch_created_at']), ('封存 UTC', record['generated_from_sealed_at']),
                ('采样数量', stats['rows']), ('API状态计数', json.dumps(stats['provider_status_counts'], ensure_ascii=False)),
                ('sckocp报告版本', ', '.join(stats['provider_versions']) or None), ('收尾组件版本', record['finalizer_version']),
                ('最大采样间隔 秒', number(stats['max_gap_s']))]
    css = '''
*{box-sizing:border-box}body{margin:0;background:#edf2f4;color:#213840;font:14px/1.6 "Noto Sans CJK SC","Microsoft YaHei",sans-serif}
main{max-width:1180px;margin:28px auto;padding:42px;background:white;box-shadow:0 4px 30px #18334014}
header{border-top:7px solid #087f8c;padding:26px 0 22px;border-bottom:1px solid #dbe5e8}.eyebrow{color:#087f8c;letter-spacing:3px;font-weight:bold;font-size:11px}
h1{margin:8px 0;font-size:32px;color:#163944}h2{margin:30px 0 14px;font-size:20px;border-left:4px solid #087f8c;padding-left:12px}h3{font-size:15px;margin:10px 0}p{margin:8px 0}
.subtle,small{color:#59717a}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0}.card{background:#eff7f7;padding:16px;border-radius:5px}.card strong{display:block;font-size:18px;color:#095c66;margin-top:6px}
.notice{background:#fff7e9;border-left:4px solid #c98720;padding:13px 17px;margin:18px 0}.table-wrap{overflow-wrap:anywhere}table{width:100%;border-collapse:collapse;font-size:12px;margin:10px 0}th{text-align:left;background:#eaf1f3;font-weight:600}th,td{padding:8px 9px;border-bottom:1px solid #dfe7ea;vertical-align:top;overflow-wrap:anywhere}tr:nth-child(even){background:#f8fafb}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:16px}.chart{border:1px solid #dce7eb;padding:10px}.chart svg{width:100%;height:auto}.chart text{font:10px sans-serif;fill:#59717a}footer{margin-top:32px;border-top:2px solid #087f8c;padding-top:14px;color:#59717a;font-size:11px}
@media(max-width:800px){main{margin:0;padding:18px}.cards,.charts{grid-template-columns:1fr 1fr}table{font-size:11px}th,td{padding:5px}}
@page{size:A4 landscape;margin:12mm}@media print{body{background:white;font-size:11px}main{margin:0;padding:0;max-width:none;box-shadow:none}.cards{grid-template-columns:repeat(4,1fr)}h1{font-size:25px}h2{break-after:avoid;margin-top:20px}thead{display:table-header-group}tr,.card,.chart,.notice{break-inside:avoid}table{font-size:9px}th,td{padding:5px}.charts{grid-template-columns:repeat(3,1fr)}a{color:inherit}}
'''
    sections = [
        '<header><div class="eyebrow">OCRUN / INTERNAL ACCEPTANCE</div><h1>机器压测报告单</h1><p class="subtle">' + esc(record['machine_key']) + ' · ' + esc(record['task_id']) + '</p></header>',
        '<div class="cards"><div class="card">任务执行<strong>' + esc(execution) + '</strong></div><div class="card">监控样本<strong>' + str(stats['rows']) + ' 条</strong></div><div class="card">报告状态<strong>已生成 / 待回执核验</strong></div><div class="card">硬件结论<strong>待人工验收</strong></div></div>',
        '<div class="notice">没有配置自动验收阈值，不自动判定机器合格。sckocp v1 的传感器有效性及读数年龄未知，零值不保证测量成功。文件交付是否完成，以独立 .finish.json 回执及实际文件校验为准。</div>',
        '<h2>01 / 批次与证据范围</h2>' + table(['项目', '记录'], metadata),
        '<h2>02 / 机器配置快照</h2>' + table(['项目', '记录'], configuration) + '<p class="subtle">' + esc(machine.get('dimm_details')) + '。快照来自任务开始时的系统可见信息，不等同于独立硬件资产认证。</p>',
        table(['块设备', '型号', '容量 GiB'], [[d['name'], d['model'], number(d['bytes'] / 1024 ** 3) if d['bytes'] is not None else None] for d in machine.get('disks', [])]),
        '<h2>03 / 压测执行过程</h2>' + table(['序号', '项目', '配置秒', '实际秒', '开始 UTC', '结束 UTC', '结束原因', '退出码', '清理确认', '工具版本'], steps),
        '<p class="subtle">达到配置时长后由执行器正常停止可能产生负的信号退出码；结合结束原因和清理确认判断。启动命令返回0不单独作为压测成功依据。上述记录不解析各工具内部自检日志，不能证明没有计算错误。</p>',
        '<h2>04 / 监控汇总</h2>' + table(['指标', '单位', '有值', '缺失', '零值', '最小', '均值', '最大'], metric_rows),
        '<p class="subtle">有值表示来源报告了数值，未独立确认传感器有效。均值为采样算术均值，不是时间加权均值或电量。风扇字段未作数值统计，原始日志继续保留。</p>',
        table(['采集任务标签', '负载有值数', 'CPU最高°C', 'CPU均值W', 'CPU最高W', '频率均值MHz'], by_task),
        '<p class="subtle">按采集标签汇总；同名重复任务合并统计，独立执行步骤仍在任务表逐项保留。</p>',
        '<div class="charts">' + ''.join(chart(stats['trend'], key, title, unit) for key, title, unit in
          [('cpu_temp_c', 'CPU 温度趋势', '°C'), ('cpu_power_w', 'CPU 功耗趋势', 'W'), ('frequency_mhz', '核心报告频率趋势', 'MHz')]) + '</div><p class="subtle">横轴按样本顺序等距；长任务按连续样本分桶取均值（最多240点），间隔异常见下节，峰值请看汇总表。</p>',
        '<h2>05 / 插槽与核心统计</h2>' + table(['插槽', '最高温度°C', '平均功耗W', '最高功耗W'], sockets) + table(['CPU编号', '报告频率均值MHz', '最高温度°C', 'C0均值%'], cores),
        '<h2>06 / 异常与待确认事项</h2><ul>' + warnings + '</ul>',
        '<h2>07 / 工具与文件追溯</h2>' + table(['序号', '任务', '实际执行程序', '程序 SHA-256', '版本'], binaries) + table(['原始结果文件', '字节数', 'SHA-256'], files),
        '<p class="subtle">完整明细见 Excel、MON 与 JSONL。本 HTML 及结构化报告 JSON 的哈希在另行发布的 .finish.json 中，避免自引用哈希。此文档先生成后上传，本页不预先宣称远端已交付。</p>',
        '<footer>OCRUN · ' + esc(SCHEMA) + ' · 批次 ' + esc(record['case']) + '<br>内部验收记录。原始数据、执行记录与人工验收结论应共同归档；本报告没有数字签名。</footer>']
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<title>机器压测报告单 - ' + esc(record['task_id']) + '</title><style>' + css + '</style></head><body><main>' + ''.join(sections) + '</main></body></html>').encode('utf-8')


def create(state, statistics):
    from common import atomic, file_hash, read
    record = make_record(state, statistics)
    base = Path(state['mon'])
    outputs = [(base.with_suffix('.report.json'), (json.dumps(record, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')),
               (base.with_suffix('.report.html'), render(record))]
    # Deterministic output anchored to sealed_at: an interrupted write can be
    # retried, but an existing different report is never overwritten.
    for path, data in outputs:
        if len(data) > MAX_REPORT_BYTES:
            raise ValueError('Detailed report exceeds 16 MiB; preserve data and split future batches')
        if path.exists() or path.is_symlink():
            if read(path, MAX_REPORT_BYTES) != data:
                raise ValueError('Existing detailed report differs; retained: ' + path.name)
        else:
            atomic(path, data, replace=False)
    return {path.name: file_hash(path) for path, unused in outputs}
