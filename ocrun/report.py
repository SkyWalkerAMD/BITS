"""Stream JSONL samples into summaries; missing readings are excluded, not zeroed."""
import argparse
import csv
import json
import math
import os

from .safety import reading

FIELDS = ("cpu_avg_mhz", "cpu_busy_mhz", "cpu_busy_percent", "cpu_temperature_c", "cpu_control_temperature_c",
          "vrm_temperature_c", "cpu_package_watts", "system_watts", "load_average_1m",
          "cpu_dram_watts", "cpu_core_active_mean_mhz", "cpu_core_active_max_mhz",
          "cpu_core_c0_mean_percent", "cpu_core_c0_max_percent", "sckocp_available")


def summarize(path):
    accumulators = {}
    rows, malformed = 0, 0
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                sample = json.loads(line)
                if not isinstance(sample, dict):
                    raise ValueError("Expected object")
            except ValueError:
                malformed += 1
                continue
            rows += 1
            values = {name: reading(sample, name) for name in FIELDS}
            for name, value in sample.get("fan_rpm", {}).items():
                values["fan:" + name] = reading(sample, "fan:" + name)
            for group in ("cpu_temperatures_c", "cpu_control_temperatures_c"):
                for name, value in sample.get(group, {}).items():
                    meta = sample.get("metric_status", {}).get(group, {}).get("readings", {}).get(name, {})
                    values[group + ":" + name] = None if meta.get("stale") or (meta.get("age_seconds") or 0) > 30 else value
            for name, value in values.items():
                item = accumulators.setdefault(name, {"valid_samples": 0, "sum": 0.0, "min": None, "max": None})
                if type(value) not in (int, float) or not math.isfinite(value):
                    continue
                item["valid_samples"] += 1
                item["sum"] += value
                item["min"] = value if item["min"] is None else min(item["min"], value)
                item["max"] = value if item["max"] is None else max(item["max"], value)
    result = []
    for name, item in sorted(accumulators.items()):
        count = item["valid_samples"]
        result.append({"metric": name, "valid_samples": count, "total_samples": rows,
                       "valid_ratio": count / rows if rows else 0,
                       "mean": item["sum"] / count if count else None,
                       "min": item["min"], "max": item["max"]})
    return {"samples": rows, "malformed_lines": malformed, "metrics": result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="metrics.jsonl")
    parser.add_argument("output", help="New .csv file, readable by Excel")
    args = parser.parse_args()
    data = summarize(args.input)
    # Exclusive create avoids silently overwriting an earlier report.
    with open(args.output, "x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("metric", "valid_samples", "total_samples", "valid_ratio", "mean", "min", "max"))
        writer.writeheader()
        writer.writerows(data["metrics"])
    print(json.dumps({"samples": data["samples"], "malformed_lines": data["malformed_lines"]}))


if __name__ == "__main__":
    main()
