"""Shared JSON contract; every sample invokes the native activation gates."""
from . import provider

SCHEMA = "sckocp-api-v1"


def collect(binary="/usr/bin/sckocp", interval=1.0, timeout=20.0, native_format="v1"):
    """Invoke sckocp for each sample, retaining its existing signed-lease policy."""
    result = provider.collect(binary, interval, timeout, native_format=native_format)
    result = dict(result)
    result["schema"] = SCHEMA
    return result
