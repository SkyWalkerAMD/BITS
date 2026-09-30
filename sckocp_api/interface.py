"""Shared JSON contract; every sample invokes the native activation gates."""
from . import provider
import time

SCHEMA = "sckocp-api-v1"


def collect(binary="/usr/bin/sckocp", interval=1.0, timeout=20.0, native_format="v1", details=False):
    """Invoke sckocp for each sample, retaining its existing signed-lease policy."""
    started = time.monotonic()
    result = provider.collect(binary, interval, timeout, native_format=native_format)
    result = dict(result)
    result["schema"] = SCHEMA
    if details and result["status"] == "ok":
        remaining = timeout - (time.monotonic() - started)
        if remaining > .1:
            result["details"] = provider.collect_details(binary, interval, remaining)
        else:
            part = provider._envelope("timeout")
            part["started_at"] = part["observed_at"]
            result["details"] = {"schema": provider.DETAILS_SCHEMA, "quality": "reported_validity_unknown",
                                 "parts": {"overview": dict(part), "info": dict(part)}}
    return result
