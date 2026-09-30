"""Single-shot local CLI; importing this module does not collect or listen."""
import argparse
import json
import signal

from . import __version__
from .interface import collect


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read licensed sckocp monitoring data as one JSON object.")
    parser.add_argument("--version", action="version", version="sckocp-api " + __version__)
    parser.add_argument("--binary", default="/usr/bin/sckocp", help="Absolute path to the existing sckocp executable")
    parser.add_argument("--format", choices=("v1", "v2"), default="v1",
                        help="Native output format; v1 works with original sckocp 1.1.0 and 1.2.0")
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling window in seconds (0.05-60)")
    parser.add_argument("--timeout", type=float, default=20.0, help="Total collection deadline in seconds (0.1-120)")
    parser.add_argument("--details", action="store_true", help="Include licensed mon/info console supplements within the same deadline")
    args = parser.parse_args(argv)
    interrupted = [130]

    def stop(signum, frame):
        interrupted[0] = 128 + signum
        raise KeyboardInterrupt()

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        options = {"native_format": args.format}
        if args.details:
            options["details"] = True
        result = collect(args.binary, args.interval, args.timeout, **options)
    except KeyboardInterrupt:
        return interrupted[0]
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    complete = result["status"] == "ok"
    if args.details:
        complete = complete and all(p["status"] == "ok" for p in result.get("details", {}).get("parts", {}).values())
    return 0 if complete else 1
