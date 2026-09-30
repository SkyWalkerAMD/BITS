"""Offline, read-only fleet snapshots. No listener, credentials or external assets."""
import csv
import datetime
import html
import json
import math
import os


def _text(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


def metric_tail(root, host, task_id, max_bytes=1024 * 1024, max_samples=240):
    from .common import identifier
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, identifier(host), identifier(task_id), "metrics.jsonl"))
    if os.path.commonpath([root, path]) != root:
        raise ValueError("Metric path escapes the server log directory")
    try:
        with open(path, "rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - max_bytes)
            stream.seek(start)
            data = stream.read(max_bytes)
        if start:
            data = data.partition(b"\n")[2]
    except OSError:
        return []
    samples = []
    for line in data.splitlines()[-max_samples:]:
        try:
            value = json.loads(line.decode("utf-8"))
            if isinstance(value, dict):
                samples.append(value)
        except (ValueError, UnicodeError):
            continue
    return samples


def _chart(samples, field, label):
    points = [(index, sample.get(field)) for index, sample in enumerate(samples)]
    points = [(index, value) for index, value in points
              if type(value) in (int, float) and math.isfinite(value)]
    if not points:
        return '<div class="chart"><b>{}</b><p>暂无有效读数</p></div>'.format(_text(label))
    low, high = min(value for _, value in points), max(value for _, value in points)
    span = max(high - low, 1)
    # Separate segments at missing readings; a gap must not look like measured data.
    segments, current, previous = [], [], None
    for index, value in points:
        if previous is not None and index != previous + 1:
            segments.append(current)
            current = []
        current.append("{:.1f},{:.1f}".format(8 + index * 344 / max(1, len(samples) - 1),
                                             92 - (value - low) * 72 / span))
        previous = index
    segments.append(current)
    drawings = []
    for segment in segments:
        if len(segment) == 1:
            x, y = segment[0].split(",")
            drawings.append('<circle cx="{}" cy="{}" r="2"/>'.format(x, y))
        else:
            drawings.append('<polyline points="{}"/>'.format(" ".join(segment)))
    return ('<div class="chart"><b>{}</b><span>{:.1f}–{:.1f}</span>'
            '<svg viewBox="0 0 360 104" role="img" aria-label="{}">{}</svg></div>').format(
                _text(label), low, high, _text(label), "".join(drawings))


def write_dashboard(output, records, log_root):
    cards = []
    for row in records:
        heartbeat = row.get("heartbeat") or {}
        latest = heartbeat.get("metrics_summary") or {}
        tasks = row.get("tasks") or []
        task = row.get("running") or (tasks[0] if tasks else {})
        result = task.get("result") or {}
        samples = metric_tail(log_root, row["host"], task["id"]) if task else []
        status = "在线" if row["online"] else "离线"
        if row.get("paused"):
            status += " · 暂停领取"
        detail = []
        for label, value in (("当前任务", task.get("id")), ("工具", task.get("tool")),
                             ("执行状态", task.get("status")), ("判定", result.get("verdict")),
                             ("原因", result.get("reason")), ("待执行", row.get("pending_count")),
                             ("心跳采样时间", latest.get("timestamp")),
                             ("心跳 CPU 温度 °C", latest.get("cpu_temperature_c")),
                             ("心跳 CPU 忙碌 %", latest.get("cpu_busy_percent")),
                             ("心跳整机功耗 W", latest.get("system_watts")),
                             ("sckocp 采集状态", (latest.get("sckocp") or {}).get("status")),
                             ("物理核心活动频率均值 MHz", latest.get("cpu_core_active_mean_mhz")),
                             ("物理核心 C0 驻留均值 %", latest.get("cpu_core_c0_mean_percent")),
                             ("日志上传错误", heartbeat.get("sync_error")),
                             ("最近上传", heartbeat.get("last_upload_at")),
                             ("曲线最后读数", samples[-1].get("timestamp") if samples else None)):
            detail.append('<dt>{}</dt><dd>{}</dd>'.format(_text(label), _text(value)))
        history = ''.join('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            _text(item["id"]), _text(item.get("tool")), _text(item.get("status")),
            _text((item.get("result") or {}).get("verdict"))) for item in tasks)
        charts = ''.join(_chart(samples, field, label) for field, label in
                         (("cpu_temperature_c", "CPU 温度 °C"), ("cpu_busy_percent", "CPU 忙碌 %"),
                          ("system_watts", "整机功耗 W")))
        cards.append('<section><h2>{} <small>{}</small></h2><dl>{}</dl><div class="charts">{}</div>'
                     '<details><summary>本页任务记录（{}）</summary><table><thead><tr>'
                     '<th>任务</th><th>工具</th><th>状态</th><th>判定</th></tr></thead>'
                     '<tbody>{}</tbody></table></details></section>'.format(
                         _text(row["host"]), _text(status), ''.join(detail), charts, len(tasks), history))
    created = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    document = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>OCRUN 设备总览</title><style>
body{font:15px system-ui,sans-serif;background:#f2f5f9;color:#17243b;margin:auto;padding:24px;max-width:1200px}
h1{margin-bottom:6px}header p{color:#56647a}section{background:#fff;border-radius:12px;padding:20px;margin:20px 0;border:1px solid #dde4ef}
h2{font-size:20px;margin-top:0}small{font-size:14px;font-weight:400;color:#53667e}
dl{display:grid;grid-template-columns:140px 1fr;gap:7px;margin-bottom:20px}dt{color:#56647a}dd{margin:0;overflow-wrap:anywhere}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}.chart{background:#f7f9fc;padding:12px;border-radius:8px}
.chart span{float:right;font-size:12px;color:#56647a}svg{display:block;width:100%;margin-top:6px}polyline{fill:none;stroke:#2563eb;stroke-width:2}circle{fill:#2563eb}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;border-bottom:1px solid #e4e8f0;padding:8px;overflow-wrap:anywhere}details{margin-top:18px}summary{cursor:pointer}
</style></head><body><header><h1>OCRUN 设备总览</h1><p>生成于 CREATED · 共 COUNT 台设备</p>
<p>这是只读快照，重新生成后才会更新。心跳读数和已上传日志可能来自不同时间，请分别查看采样时间。曲线来自服务器已收到的日志，可能滞后于实际运行；横轴为最近采样顺序，缺失读数留空。执行完成不等于硬件合格。</p></header>CARDS</body></html>'''
    document = document.replace("CREATED", _text(created)).replace("COUNT", str(len(records))).replace("CARDS", ''.join(cards))
    with open(output, "x", encoding="utf-8") as stream:
        stream.write(document)


def _csv_value(value):
    value = "" if value is None else str(value)
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value


def write_fleet_csv(output, records):
    fields = ("host", "online", "paused", "task_id", "batch_id", "tool", "status", "verdict",
              "reason", "created_at", "started_at", "finished_at")
    with open(output, "x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in records:
            for task in row.get("tasks") or [{}]:
                values = dict(task, host=row["host"], online=row["online"], paused=row.get("paused"),
                              task_id=task.get("id"), verdict=(task.get("result") or {}).get("verdict"),
                              reason=(task.get("result") or {}).get("reason"))
                writer.writerow({key: _csv_value(values.get(key)) for key in fields})
