"""Bounded-memory long-log XLSX: complete rows, split sheets, bounded trend data."""
import csv
import fcntl
import hashlib
import math
import os
from pathlib import Path

from common import directory, regular

MAX_ROWS = 4000000
SHEET_ROWS = 250000
MAX_BYTES = 16 * 1024 ** 3


def render(source, output, sheet_name):
    import xlsxwriter
    fd = os.open(str(source), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    checksum = hashlib.sha256()
    stats, trend, bucket = {}, [], []
    rows = 0
    before = os.fstat(fd)
    regular(before, path=source)
    workbook = xlsxwriter.Workbook(str(output), {'constant_memory': True,
        'strings_to_formulas': False, 'strings_to_urls': False, 'tmpdir': str(output.parent)})
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            header = stream.readline(8193)
            columns = stream.readline(8193)
            if not header.startswith(b'#-') or not columns.startswith(b'#=') or len(columns) > 8192:
                raise ValueError('Invalid monitoring headers')
            checksum.update(header + columns)
            names = next(csv.reader([columns.decode('utf-8')[2:].strip()]))
            if len(names) != 11:
                raise ValueError('Expected 11 column names')
            table = None
            while True:
                line = stream.readline(16385)
                if not line:
                    break
                if len(line) > 16384 or not line.endswith(b'\n') or stream.tell() > MAX_BYTES:
                    raise ValueError('Monitoring row/byte limit exceeded')
                checksum.update(line)
                row = next(csv.reader([line.decode('utf-8')], strict=True))
                if len(row) != 11 or any(len(cell) > 1024 or any(ord(c) < 32 for c in cell) for cell in row):
                    raise ValueError('Invalid monitoring row')
                if rows >= MAX_ROWS:
                    raise ValueError('Monitoring row budget exceeded')
                if rows % SHEET_ROWS == 0:
                    suffix = '' if rows == 0 else '-{:02d}'.format(rows // SHEET_ROWS + 1)
                    table = workbook.add_worksheet(sheet_name[:31-len(suffix)] + suffix)
                    table.write_row(0, 0, names)
                    table.freeze_panes(1, 3)
                    table.set_column(0, 2, 23)
                    table.set_column(3, 10, 16)
                converted = row[:3]
                for cell in row[3:]:
                    if not cell:
                        converted.append(None)
                    else:
                        try:
                            value = float(cell)
                        except ValueError:
                            value = cell
                        if isinstance(value, float) and not math.isfinite(value):
                            raise ValueError('Non-finite measurement')
                        converted.append(value)
                table.write_row(rows % SHEET_ROWS + 1, 0, converted)
                task = row[2]
                if task not in stats:
                    if len(stats) >= 10000:
                        raise ValueError('Too many task labels')
                    stats[task] = {'rows': 0, 'values': [[0, 0.0, None, None] for _ in range(8)]}
                aggregate = stats[task]
                aggregate['rows'] += 1
                for index, value in enumerate(converted[3:]):
                    if isinstance(value, (float, int)):
                        metric = aggregate['values'][index]
                        metric[0] += 1
                        metric[1] += value
                        metric[2] = value if metric[2] is None else min(metric[2], value)
                        metric[3] = value if metric[3] is None else max(metric[3], value)
                bucket.append([converted[n] for n in (5, 6, 8)])
                rows += 1
                if len(bucket) == 1000:
                    trend.append([rows] + [sum(v[i] for v in bucket if isinstance(v[i], (int, float))) /
                        sum(isinstance(v[i], (int, float)) for v in bucket) if any(isinstance(v[i], (int, float)) for v in bucket)
                        else None for i in range(3)])
                    bucket = []
            if bucket:
                trend.append([rows] + [sum(v[i] for v in bucket if isinstance(v[i], (int, float))) /
                    sum(isinstance(v[i], (int, float)) for v in bucket) if any(isinstance(v[i], (int, float)) for v in bucket)
                    else None for i in range(3)])
        if rows == 0:
            raise ValueError('No monitoring rows')
        summary_name = 'Task Summary' if sheet_name != 'Task Summary' else 'Task Summary 2'
        summary = workbook.add_worksheet(summary_name)
        summary.write_row(0, 0, ['Task', 'Samples', 'Metric', 'Available', 'Mean', 'Min', 'Max'])
        offset = 1
        for task, aggregate in sorted(stats.items()):
            for index, metric in enumerate(aggregate['values']):
                n, total, low, high = metric
                summary.write_row(offset, 0, [task, aggregate['rows'], names[index+3], n,
                                              total / n if n else None, low, high])
                offset += 1
        summary.write(offset+1, 0, 'Missing values remain blank. Provider v1 validity and age are unknown. No hardware pass/fail verdict.')
        trend_name = 'Trend' if sheet_name != 'Trend' else 'Trend 2'
        trend_sheet = workbook.add_worksheet(trend_name)
        trend_sheet.write_row(0, 0, ['Last sample in bucket', 'Mean MHz', 'Mean CPU C', 'Mean package W'])
        for i, values in enumerate(trend, 1):
            trend_sheet.write_row(i, 0, values)
        for column in range(1, 4):
            chart = workbook.add_chart({'type': 'line'})
            chart.add_series({'categories': [trend_name, 1, 0, len(trend), 0],
                              'values': [trend_name, 1, column, len(trend), column]})
            chart.set_title({'name': ['Frequency MHz', 'CPU temperature C', 'Package W'][column-1]})
            trend_sheet.insert_chart((column-1)*16, 6, chart)
        after = source.lstat()
        key = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if key(before) != key(after):
            raise ValueError('Source changed while reporting')
    finally:
        workbook.close()
        os.close(fd)
    return rows, checksum.hexdigest()
