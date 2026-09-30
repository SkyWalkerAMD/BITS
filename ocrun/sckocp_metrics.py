"""Merge validated sckocp readings while retaining their individual freshness."""


def merge_sample(result, envelope, meta, mapping):
    result["source_status"]["sckocp"] = meta
    result["sckocp"] = envelope or {
        "schema": "ocrun-sckocp-v1", "status": meta.get("error") or meta["status"],
        "observed_at": None, "data": None, "error": meta.get("error")}
    if meta.get("error") or meta["stale"]:
        result["errors"]["sckocp"] = meta.get("error") or "Reading " + meta["status"]
    data = envelope.get("data") if envelope else None
    unavailable = dict(meta, stale=True, status="unavailable")
    overrides = {}

    def put(name, value, source, status):
        result[name] = value
        result["sources"][name] = source if value is not None else None
        result["metric_status"][name] = dict(status if value is not None else unavailable)

    def reading(item):
        if not item or item["status"] != "ok" or meta["stale"]:
            return None
        # Native age already includes collection time; add only time since receipt.
        age = meta["age_seconds"] + item["age_s"]
        if age > meta["max_age_seconds"]:
            return None
        return item["value"], dict(meta, age_seconds=round(age, 3), stale=False)

    put("sckocp_available", 1 if data else None, "sckocp:api", meta)
    for name in ("cpu_core_active_mean_mhz", "cpu_core_active_max_mhz", "cpu_core_c0_mean_percent",
                 "cpu_core_c0_max_percent", "cpu_dram_watts"):
        put(name, None, None, unavailable)
    if not data:
        return

    for field, group in (("temperature_c", "cpu_temperatures_c"),
                         ("control_temperature_c", "cpu_control_temperatures_c")):
        found = [reading(socket["metrics"][field]) for socket in data["sockets"]]
        # A partial maximum can hide the hottest socket. Keep partial readings
        # in the raw provider payload, never in the protection candidates.
        if found and all(item is not None for item in found):
            for socket, item in zip(data["sockets"], found):
                source = "sckocp:socket{}:{}".format(socket["id"], field)
                result[group][source] = item[0]
                overrides[source] = item[1]

    for group, scalar in (("cpu_temperatures_c", "cpu_temperature_c"),
                          ("cpu_control_temperatures_c", "cpu_control_temperature_c")):
        readings = result[group]
        details = result["metric_status"][group]["readings"]
        details.update({source: overrides[source] for source in readings if source in overrides})
        if details:
            oldest = max(details.values(), key=lambda status: status.get("age_seconds") or 0)
            result["metric_status"][group] = dict(oldest, readings=details)
        if readings and not (scalar == "cpu_temperature_c" and mapping.get("cpu_temperature")):
            hottest = max(readings, key=readings.get)
            put(scalar, readings[hottest], hottest, details[hottest])

    def aggregate(items, field, name, function, fallback=False):
        found = [reading(item["metrics"][field]) for item in items]
        # An incomplete socket/core set must not produce a plausible partial total/mean.
        if not found or any(value is None for value in found):
            return
        if fallback and result.get(name) is not None:
            return
        oldest = max((value[1] for value in found), key=lambda status: status["age_seconds"])
        put(name, function([value[0] for value in found]), "sckocp:" + field, oldest)

    aggregate(data["sockets"], "package_watts", "cpu_package_watts", sum, fallback=True)
    aggregate(data["sockets"], "dram_watts", "cpu_dram_watts", sum)
    aggregate([data["system"]], "psu_input_watts", "system_watts", sum, fallback=True)
    for field, prefix in (("active_mhz", "cpu_core_active"), ("c0_percent", "cpu_core_c0")):
        unit = "mhz" if field == "active_mhz" else "percent"
        aggregate(data["cores"], field, prefix + "_mean_" + unit, lambda values: sum(values) / len(values))
        aggregate(data["cores"], field, prefix + "_max_" + unit, max)
