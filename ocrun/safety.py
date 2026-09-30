"""Local, latched protection rules; task rules may only tighten host limits."""
import math


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_protection(settings):
    if not isinstance(settings, dict):
        raise ValueError("protection must be an object")
    allowed = {"limits", "stop_on_ipmi_critical", "missing_samples", "max_metric_age_seconds", "max_sample_gap_seconds"}
    if set(settings) - allowed:
        raise ValueError("Unknown protection setting")
    if "stop_on_ipmi_critical" in settings and type(settings["stop_on_ipmi_critical"]) is not bool:
        raise ValueError("stop_on_ipmi_critical must be boolean")
    for name in ("missing_samples", "max_metric_age_seconds", "max_sample_gap_seconds"):
        if name in settings and (not finite(settings[name]) or settings[name] <= 0):
            raise ValueError("Invalid protection " + name)
    if "missing_samples" in settings and type(settings["missing_samples"]) is not int:
        raise ValueError("missing_samples must be an integer")
    limits = settings.get("limits", {})
    if not isinstance(limits, dict):
        raise ValueError("limits must be an object")
    for metric, rule in limits.items():
        if not isinstance(metric, str) or not isinstance(rule, dict) or not rule:
            raise ValueError("Invalid protection limit")
        if set(rule) - {"min", "max", "critical_min", "critical_max", "consecutive"}:
            raise ValueError("Unknown limit field for " + metric)
        if not set(rule).intersection({"min", "max", "critical_min", "critical_max"}):
            raise ValueError("Limit needs a numerical bound")
        for name, value in rule.items():
            if not finite(value):
                raise ValueError("Invalid bound for " + metric)
            if name == "consecutive" and (type(value) is not int or value < 1):
                raise ValueError("consecutive must be a positive integer")
        if rule.get("min", -math.inf) > rule.get("max", math.inf):
            raise ValueError("Limit minimum exceeds maximum")
    return settings


def protection_policy(host=None, task=None):
    host, task = validate_protection(host or {}), validate_protection(task or {})
    result = {"stop_on_ipmi_critical": host.get("stop_on_ipmi_critical", True),
              "missing_samples": host.get("missing_samples", 3),
              "max_metric_age_seconds": host.get("max_metric_age_seconds", 30),
              "max_sample_gap_seconds": host.get("max_sample_gap_seconds", 30), "limits": {}}
    result["stop_on_ipmi_critical"] |= task.get("stop_on_ipmi_critical", False)
    for name in ("missing_samples", "max_metric_age_seconds", "max_sample_gap_seconds"):
        result[name] = min(result[name], task.get(name, result[name]))
    for index, source in enumerate((host, task)):
        for metric, rule in source.get("limits", {}).items():
            merged = result["limits"].setdefault(metric, {})
            if index == 0:
                merged["consecutive"] = rule.get("consecutive", 3)
            for name, value in rule.items():
                if name in merged:
                    value = max(value, merged[name]) if name in ("min", "critical_min") else min(value, merged[name])
                merged[name] = value
    return validate_protection(result)


def reading_info(sample, name, maximum_age=30):
    statuses = sample.get("metric_status", {})
    status = statuses.get(name, statuses.get("fan_rpm", {}) if name.startswith("fan:") else {})
    if name.startswith("fan:"):
        value = sample.get("fan_rpm", {}).get(name[4:])
    else:
        value = sample.get(name)
    candidates = [(value, status)]
    if name == "cpu_temperature_c":
        details = statuses.get("cpu_temperatures_c", {}).get("readings", {})
        candidates += [(value, details.get(source, {})) for source, value in sample.get("cpu_temperatures_c", {}).items()]
    current = [(value, meta) for value, meta in candidates if finite(value) and not meta.get("stale")
               and (meta.get("age_seconds") is None or meta["age_seconds"] <= maximum_age)]
    return max(current, key=lambda item: item[0]) if current else (None, status)


def reading(sample, name, maximum_age=30):
    return reading_info(sample, name, maximum_age)[0]


class SafetyMonitor:
    def __init__(self, policy, required):
        self.policy = policy
        self.required = set(required) | set(policy["limits"])
        self.missing = {}
        self.exceeded = {}
        self.observed = {}
        self.failure = None

    def check(self, sample, preflight=False):
        if self.failure:
            return self.failure
        def fail(code, reason, **details):
            self.failure = dict(code=code, reason=reason, **details)
            return self.failure
        if self.policy["stop_on_ipmi_critical"]:
            critical = [name for name, data in sample.get("ipmi_sensors", {}).items()
                        if str(data.get("status", "")).lower() in ("cr", "nr")]
            if critical:
                return fail("sensor_critical", "Critical hardware sensor alarm", sensors=critical)
        for name in sorted(self.required):
            value, reading_status = reading_info(sample, name, self.policy["max_metric_age_seconds"])
            self.missing[name] = self.missing.get(name, 0) + 1 if value is None else 0
            if value is None:
                if preflight or self.missing[name] >= self.policy["missing_samples"]:
                    return fail("metric_unavailable", "Required reading missing or stale: " + name, metric=name)
                continue
            rule = self.policy["limits"].get(name, {})
            for bound, compare in (("critical_max", lambda a, b: a > b), ("critical_min", lambda a, b: a < b)):
                if bound in rule and compare(value, rule[bound]):
                    return fail("critical_limit", "Critical limit exceeded: " + name, metric=name, value=value, limit=rule[bound])
            outside = value > rule.get("max", math.inf) or value < rule.get("min", -math.inf)
            observed = reading_status.get("observed_at")
            fresh_observation = observed is None or self.observed.get(name) != observed
            if fresh_observation:
                self.exceeded[name] = self.exceeded.get(name, 0) + 1 if outside else 0
                self.observed[name] = observed
            if outside and (preflight or self.exceeded.get(name, 0) >= rule.get("consecutive", 3)):
                return fail("limit_exceeded", "Protection limit exceeded: " + name, metric=name, value=value, limits=rule)
        return None
