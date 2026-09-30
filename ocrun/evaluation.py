"""Evidence-based acceptance of an explicit test profile, never hardware certification."""
import json
import os
import re

from .safety import finite, reading


def validate_acceptance(profile):
    if not isinstance(profile, dict):
        raise ValueError("acceptance must be an object")
    allowed = {"name", "metrics", "minimum_samples", "minimum_valid_ratio", "minimum_in_range_ratio",
               "warmup_seconds", "minimum_duration_seconds", "max_sample_gap_seconds", "require_tool_verification"}
    if set(profile) - allowed:
        raise ValueError("Unknown acceptance setting")
    if "name" in profile and (not isinstance(profile["name"], str) or not profile["name"].strip()):
        raise ValueError("Invalid acceptance profile name")
    for name in ("minimum_valid_ratio", "minimum_in_range_ratio"):
        if name in profile and (not finite(profile[name]) or not 0 < profile[name] <= 1):
            raise ValueError("Invalid " + name)
    for name in ("warmup_seconds", "minimum_duration_seconds", "max_sample_gap_seconds"):
        if name in profile and (not finite(profile[name]) or profile[name] < 0):
            raise ValueError("Invalid " + name)
    if "minimum_samples" in profile and (type(profile["minimum_samples"]) is not int or profile["minimum_samples"] < 1):
        raise ValueError("minimum_samples must be positive integer")
    if "require_tool_verification" in profile and type(profile["require_tool_verification"]) is not bool:
        raise ValueError("require_tool_verification must be boolean")
    rules = profile.get("metrics", {})
    if not isinstance(rules, dict):
        raise ValueError("acceptance metrics must be an object")
    for name, rule in rules.items():
        if not isinstance(name, str) or not isinstance(rule, dict) or not rule or set(rule) - {"min", "max"}:
            raise ValueError("Invalid acceptance metric")
        if any(not finite(value) for value in rule.values()) or rule.get("min", float("-inf")) > rule.get("max", float("inf")):
            raise ValueError("Invalid acceptance bounds")
    return profile


class ErrorScanner:
    """Incremental bounded reads: a stopped worker can fail while its parent stays alive."""
    def __init__(self, directory, tool):
        self.directory, self.tool = directory, tool
        self.positions, self.tails = {}, {}
        self.identities = {}
        self.errors = []
        self.complete = True
        self.patterns = [re.compile(r"FATAL ERROR|(?:possible )?hardware failure|rounding (?:was|error)|sumout error", re.I)] if tool.startswith("p95-") else []
        if tool == "stress-ng":
            self.patterns = [re.compile(r"stress-ng:\s*(?:fail|error):", re.I), re.compile(r"verification fail(?:ed|ure)", re.I)]

    def scan(self, final=False):
        if not self.patterns:
            return self.errors
        paths = ["workload.log"] + (["work/results.txt", "work/prime.log"] if self.tool.startswith("p95-") else [])
        budget = 32 * 1024 * 1024 if final else 512 * 1024
        self.complete = True
        for relative in paths:
            path = os.path.join(self.directory, relative)
            try:
                with open(path, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    identity = (info.st_dev, info.st_ino)
                    if self.identities.get(relative) != identity or info.st_size < self.positions.get(relative, 0):
                        self.positions[relative] = 0
                        self.tails[relative] = ""
                    self.identities[relative] = identity
                    stream.seek(self.positions.get(relative, 0))
                    data = stream.read(budget)
                    self.positions[relative] = stream.tell()
                    if stream.read(1):
                        self.complete = False
                text = self.tails.get(relative, "") + data.decode("utf-8", errors="replace")
                for pattern in self.patterns:
                    match = pattern.search(text)
                    if match and len(self.errors) < 20:
                        evidence = {"file": relative, "message": text[max(0, match.start() - 40):match.end() + 160].split("\n")[0]}
                        if evidence not in self.errors:
                            self.errors.append(evidence)
                self.tails[relative] = text[-256:]
            except FileNotFoundError:
                pass
        return self.errors


def evaluate(directory, task, status, result, profile=None, scanner=None):
    profile = validate_acceptance(profile or {})
    evidence = {"scope": "configured_profile_only", "profile": profile.get("name"), "issues": [], "metrics": {}}
    errors = scanner.scan(final=True) if scanner else []
    evidence["tool_errors"] = errors
    if status == "failed" or status == "timed_out" or errors:
        evidence["issues"].append(result.get("reason", "Execution or workload verification failed"))
        return {"verdict": "failed", "acceptance": evidence}
    if status != "completed":
        evidence["issues"].append("Execution did not complete")
    if not profile.get("name") or not profile.get("metrics"):
        evidence["issues"].append("No explicit named acceptance profile with metric bounds")
    if scanner and not scanner.complete:
        evidence["issues"].append("Workload error log scan incomplete")
    if profile.get("require_tool_verification", True):
        # No full worker-count/completion parser is provided for bundled tools yet.
        evidence["issues"].append("Full workload-specific verification is unavailable")
    if result.get("elapsed_seconds", 0) < profile.get("minimum_duration_seconds", 0):
        evidence["issues"].append("Insufficient execution duration")
    rules = profile.get("metrics", {})
    rows, malformed, previous, largest_gap, last_seen = 0, 0, None, 0, None
    accumulators = {name: {"valid": 0, "in_range": 0, "min": None, "max": None} for name in rules}
    try:
        with open(os.path.join(directory, "metrics.jsonl"), encoding="utf-8") as stream:
            for line in stream:
                try:
                    sample = json.loads(line)
                    elapsed = sample.get("elapsed_seconds")
                    if not isinstance(sample, dict) or not finite(elapsed):
                        raise ValueError("Missing elapsed time")
                    if elapsed < 0 or elapsed > result.get("elapsed_seconds", 0) + 0.002 or (last_seen is not None and elapsed <= last_seen):
                        raise ValueError("Invalid sample timeline")
                    last_seen = elapsed
                    if elapsed < profile.get("warmup_seconds", 0):
                        continue
                except (ValueError, AttributeError):
                    malformed += 1
                    continue
                rows += 1
                largest_gap = max(largest_gap, elapsed - (previous if previous is not None else profile.get("warmup_seconds", 0)))
                previous = elapsed
                for name, rule in rules.items():
                    value = reading(sample, name)
                    if value is None:
                        continue
                    item = accumulators[name]
                    item["valid"] += 1
                    item["in_range"] += rule.get("min", float("-inf")) <= value <= rule.get("max", float("inf"))
                    item["min"] = value if item["min"] is None else min(item["min"], value)
                    item["max"] = value if item["max"] is None else max(item["max"], value)
    except OSError:
        evidence["issues"].append("Metrics file unavailable")
    largest_gap = max(largest_gap, result.get("elapsed_seconds", 0) - (previous if previous is not None else 0))
    evidence.update(samples=rows, malformed_lines=malformed, max_sample_gap_seconds=round(largest_gap, 3))
    if malformed or rows < profile.get("minimum_samples", 3):
        evidence["issues"].append("Too few valid samples or malformed metrics")
    if largest_gap > profile.get("max_sample_gap_seconds", 30):
        evidence["issues"].append("Monitoring sample gap exceeds acceptance limit")
    violated = False
    for name, item in accumulators.items():
        item["valid_ratio"] = item["valid"] / rows if rows else 0
        item["in_range_ratio"] = item["in_range"] / item["valid"] if item["valid"] else 0
        evidence["metrics"][name] = item
        if item["valid_ratio"] < profile.get("minimum_valid_ratio", 0.95):
            evidence["issues"].append("Missing or stale metric: " + name)
        elif item["in_range_ratio"] < profile.get("minimum_in_range_ratio", 0.95):
            evidence["issues"].append("Metric outside acceptance bounds: " + name)
            violated = True
    verdict = "failed" if violated else ("insufficient_data" if evidence["issues"] else "passed")
    return {"verdict": verdict, "acceptance": evidence}
