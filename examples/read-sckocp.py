"""Example client: query this activated device and select socket temperatures."""
import argparse
import json

from sckocp_api import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="/usr/bin/sckocp")
    parser.add_argument("--format", choices=("v1", "v2"), default="v1")
    args = parser.parse_args()
    result = collect(binary=args.binary, interval=1, timeout=20, native_format=args.format)
    if result["status"] != "ok":
        print(json.dumps({"status": result["status"], "error": result["error"]}))
        return 1
    temperatures = []
    for socket in result["data"]["sockets"]:
        if args.format == "v1":
            value = socket.get("temp_max_c")
            temperatures.append({"socket_id": socket["id"], "temperature": value,
                                 "unit": "C", "status": "reported" if value is not None else "unavailable",
                                 "age_seconds": None})
        else:
            metric = socket["metrics"]["temperature_c"]
            temperatures.append({"socket_id": socket["id"], "temperature": metric["value"],
                                 "unit": metric["unit"], "status": metric["status"],
                                 "age_seconds": metric["age_s"]})
    print(json.dumps({"observed_at": result["observed_at"],
                      "provider_schema": result["data"]["schema"],
                      "temperatures": temperatures}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
