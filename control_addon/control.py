"""Original controller extension: local result inspection, no service or task writes."""
import argparse
import contextlib
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from . import VERSION
from .data import Reader, MAX_FILE, decode, parts

SCHEMA = "mon-sensors-control-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
FIELDS = ("type", "time", "task", "uptime_s", "load1", "frequency_mhz",
          "cpu_temp_c", "vrm_temp_c", "cpu_power_w", "system_power_w", "fan")


def receipt(reader, relative):
    names = parts(relative)
    if len(names) != 2 or not names[-1].endswith(".finish.json"):
        raise ValueError("Select MACHINE/BATCH.finish.json")
    raw = reader.read(relative)
    value = decode(raw)
    if not isinstance(value, dict) or value.get("schema") not in ("mon-sensors-finish-receipt-v1", "mon-sensors-finish-receipt-v2"):
        raise ValueError("Unknown completion receipt schema")
    for name in ("case", "task_id", "task_time"):
        if not isinstance(value.get(name), str) or len(parts(value[name])) != 1:
            raise ValueError("Invalid receipt field: " + name)
    if not re.fullmatch(r"[0-9a-f]{32}", value["case"]):
        raise ValueError("Invalid case identifier")
    base = names[-1][:-len(".finish.json")]
    if base != names[0] + "_" + value["task_id"] + "_" + value["task_time"]:
        raise ValueError("Receipt identity and filename disagree")
    expected = {base + suffix for suffix in (".mon", ".mon.sckocp.jsonl", ".xlsx")}
    if value['schema'] == 'mon-sensors-finish-receipt-v2':
        if value.get('detailed_report_schema') != 'ocrun-acceptance-report-v1':
            raise ValueError('Unknown detailed report schema')
        expected.update(base + suffix for suffix in ('.report.html', '.report.json'))
    files = value.get("artifacts")
    if not isinstance(files, dict) or set(files) != expected:
        raise ValueError("Receipt artifact list differs from the declared batch schema")
    for name, spec in files.items():
        if (not isinstance(spec, dict) or type(spec.get("bytes")) is not int or
                not 0 < spec["bytes"] <= MAX_FILE or not isinstance(spec.get("sha256"), str) or
                not HEX.fullmatch(spec["sha256"])):
            raise ValueError("Invalid artifact size or hash: " + name)
    return value, hashlib.sha256(raw).hexdigest()


def inspect(reader, relative, verify=False, expected=None):
    value, digest = receipt(reader, relative)
    if expected is not None and (not HEX.fullmatch(expected) or digest != expected):
        raise ValueError("Receipt SHA-256 differs from the trusted node record")
    legacy = "execution_result" not in value
    result = {"receipt": relative, "receipt_sha256": digest,
              "receipt_trust": "matches_supplied_sha256" if expected else "unsigned_unpinned",
              "case": value["case"], "task_id": value["task_id"], "task_time": value["task_time"],
              "execution": value.get("execution_result", "legacy_launch_status_only"),
              "data_quality": value.get("data_quality", "legacy_quality_not_recorded"),
              "report": value.get("report_result", "legacy_report_listed_not_verified_here"),
              "delivery_claim": value.get("delivery_result", "legacy_receipt_present"),
              "verification_method_claim": value.get("verification", "unknown"),
              "verified_here": False, "hardware_result": "not_assessed", "legacy_receipt": legacy,
              "samples": value.get("samples", {}), "steps": value.get("steps", []),
              "artifacts": value["artifacts"], "data_verified_at_claim": value.get("data_verified_at"),
              "report_path": reader.root + "/" + relative[:-len(".finish.json")] + ".xlsx"}
    result['html_report_path'] = (reader.root + '/' + relative[:-len('.finish.json')] + '.report.html'
                                  if value['schema'] == 'mon-sensors-finish-receipt-v2' else None)
    if verify:
        root = parts(relative)[0]
        checked = {}
        # Keep initial descriptors through the entire operation. The context
        # checks identity/stat again on exit, including files hashed earlier.
        with contextlib.ExitStack() as opened:
            for name in value["artifacts"]:
                opened.enter_context(reader.file(root + "/" + name))
            for name, spec in sorted(value["artifacts"].items()):
                actual = reader.digest(root + "/" + name)
                if actual != {"bytes": spec["bytes"], "sha256": spec["sha256"]}:
                    raise ValueError("Result size or SHA-256 mismatch: " + name)
                checked[name] = actual
            if receipt(reader, relative)[1] != digest:
                raise ValueError("Receipt changed during verification")
        result.update(verified_here=True, verification="local-streaming-sha256", checked=checked)
    return result


def listing(reader, machine, limit=20):
    if len(parts(machine)) != 1:
        raise ValueError("Select one MACHINE directory, such as K6C-165_SERIAL")
    bases = set()
    with reader.directory(machine) as directory:
        # scandir(fd) was added after Python 3.6; procfs names our pinned FD.
        with os.scandir("/proc/self/fd/" + str(directory)) as entries:
            for count, entry in enumerate(entries, 1):
                if count > 20000:
                    raise ValueError("More than 20000 directory entries; select a receipt explicitly")
                suffix = next((s for s in (".finish.json", ".mon") if entry.name.endswith(s)), None)
                if suffix:
                    parts(entry.name)
                    bases.add(entry.name[:-len(suffix)])
    result = []
    for base in sorted(bases, reverse=True)[:limit]:
        relative = machine + "/" + base + ".finish.json"
        try:
            row = inspect(reader, relative)
            row["record"] = "receipt_present"
        except FileNotFoundError:
            row = {"receipt": relative, "record": "no_receipt", "verified_here": False,
                   "next": "检查节点 mon-sensors-finish status；没有回执不代表任务正在运行或已经成功。"}
        except (OSError, ValueError, TypeError) as exc:
            row = {"receipt": relative, "record": "invalid_receipt", "error": str(exc), "verified_here": False}
        result.append(row)
    return {"machine": machine, "order": "filename_descending_not_execution_time",
            "total_batches": len(bases), "returned": len(result), "batches": result}


def sample(reader, relative):
    if len(parts(relative)) != 2 or not relative.endswith(".mon"):
        raise ValueError("Select MACHINE/BATCH.mon")
    rows = [r for r in reader.tail(relative, 65536) if r and not r.startswith(b"#")]
    if not rows:
        raise ValueError("No complete sample line is available")
    values = next(csv.reader([rows[-1].decode("utf-8")]))
    if len(values) != 11:
        raise ValueError("Expected the original eleven-column MON format")
    row = dict(zip(FIELDS, [None if v == "" else v for v in values]))
    result = {"mon": relative, "sample": row, "quality": "unknown",
              "freshness": "uploaded_snapshot_not_live_node_connection",
              "hardware_result": "not_assessed", "sidecar_alignment": "absent", "detail": None}
    try:
        lines = reader.tail(relative + ".sckocp.jsonl")
    except FileNotFoundError:
        return result
    result["sidecar_alignment"] = "no_matching_sample_in_tail"
    # A provider may flush JSON before CSV. Only expose detail belonging to the
    # selected row; raw string values and nulls remain intact.
    for line in reversed(lines[-256:]):
        item = decode(line)
        if (not isinstance(item, dict) or item.get("schema") != "mon-sensors-sckocp-v1" or
                not isinstance(item.get("os"), dict)):
            raise ValueError("Unknown monitoring attachment schema")
        if item["os"].get("time") == row["time"] and item["os"].get("task") == row["task"]:
            result.update(sidecar_alignment="time_and_task_match", detail=item,
                          quality=item.get("reading_quality", "unknown"))
            break
    return result


def check(app, results_root):
    baseline = decode((Path(__file__).parent / "baseline.json").read_bytes())
    comparison = {}
    with Reader(app) as reader:
        for name, expected in baseline["files"].items():
            try:
                actual = reader.digest(name)["sha256"]
                comparison[name] = {"match": actual == expected, "sha256": actual, "expected": expected}
            except (OSError, ValueError) as exc:
                comparison[name] = {"match": False, "error": str(exc), "expected": expected}
    with Reader(results_root):
        pass
    return {"app": app, "results_root": results_root, "baseline": baseline["release"],
            "original_files_match": all(v["match"] for v in comparison.values()), "files": comparison,
            "python": sys.version.split()[0], "read_only": True, "management_program_modified": False,
            "note": "只读文件适配；版本匹配不等于现场部署和硬件验收。"}


def clean(value):
    # Result files can contain hostile terminal escapes. JSON also escapes them.
    return "".join(c if c.isprintable() else "?" for c in str(value))[:1500]


def human(result):
    if "batches" in result:
        print("主机={}，批次数={}，显示={}（文件名倒序）".format(result["machine"], result["total_batches"], result["returned"]))
        for row in result["batches"]:
            human(row)
    elif "sample" in result:
        row = result["sample"]
        print("样本={} 任务={} 主频={}MHz CPU温度={}°C CPU功耗={}W".format(
            *[clean(row.get(k)) for k in ("time", "task", "frequency_mhz", "cpu_temp_c", "cpu_power_w")]))
        print("缺失读数保留空值；质量={}；这是已上传样本，不保证实时。".format(clean(result["quality"])))
        print("详细 JSON：同一命令追加 --json；附件匹配={}".format(result["sidecar_alignment"]))
    elif "receipt" in result:
        print(clean(result["receipt"]))
        if result.get("record") in ("no_receipt", "invalid_receipt"):
            print("  记录={}；{}".format(result["record"], clean(result.get("error", result.get("next")))))
        else:
            print("  执行={} 数据={} 报表={} 交付声明={}".format(
                *[clean(result[k]) for k in ("execution", "data_quality", "report", "delivery_claim")]))
            print("  本机文件核验={} 回执信任={}；不作为硬件合格结论。".format(
                "通过" if result["verified_here"] else "尚未执行 verify", result["receipt_trust"]))
            print("  报表文件=" + clean(result["report_path"]))
            if result.get('html_report_path'):
                print('  详细报告=' + clean(result['html_report_path']))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="mon-sensors-control " + VERSION)
    sub = parser.add_subparsers(dest="command")
    for command in ("check", "list", "show", "verify", "sample"):
        child = sub.add_parser(command)
        child.add_argument("--results-root", default="/data/cds/result")
        child.add_argument("--json", action="store_true")
        if command == "check":
            child.add_argument("--app", default="/home/ocuser/ocrun")
        if command == "list":
            child.add_argument("--machine", required=True)
            child.add_argument("--limit", type=int, choices=range(1, 101), default=20, metavar="1..100")
        if command in ("show", "verify"):
            child.add_argument("--receipt", required=True)
            child.add_argument("--receipt-sha256")
        if command == "sample":
            child.add_argument("--mon", required=True)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "check":
            value = check(args.app, args.results_root)
        else:
            with Reader(args.results_root) as reader:
                if args.command == "list":
                    value = listing(reader, args.machine, args.limit)
                elif args.command == "sample":
                    value = sample(reader, args.mon)
                else:
                    value = inspect(reader, args.receipt, args.command == "verify", args.receipt_sha256)
        value.update(schema=SCHEMA, version=VERSION)
        if args.json:
            print(json.dumps(value, ensure_ascii=True, sort_keys=True))
        else:
            human(value)
        return 0 if value.get("original_files_match", True) else 2
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        error = {"schema": SCHEMA, "status": "error", "error": str(exc), "version": VERSION}
        print(json.dumps(error, ensure_ascii=True) if args.json else "mon-sensors-control: " + clean(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
