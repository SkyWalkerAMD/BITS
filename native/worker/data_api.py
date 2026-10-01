"""The only sckocp invocation surface used by independent BITS.

This module exposes sampling, not a command runner. Enrollment, batch input and
network requests cannot set an executable, operation, environment or arguments.
The native program remains solely responsible for authorization.
"""
import shutil

from sckocp_api.interface import collect
from sckocp_api.provider import GATE_STATUS


def sample(include_details=False):
    if type(include_details) is not bool:
        raise ValueError("Sampling accepts only the supplemental-data flag")
    binary = shutil.which("sckocp", path="/usr/local/bin:/usr/bin")
    if binary is None:
        raise ValueError("Original sckocp data provider is unavailable")
    # collect accepts only mon JSON and fixed mon/info supplements. Its output
    # whitelist removes non-Primary timing groups before they reach BITS.
    value = collect(binary=binary, interval=1, timeout=20,
                    native_format="v1", details=include_details)
    # Authorization can change between the base read and a supplement. Do not
    # retain the earlier successful reading after the native provider refused
    # any part of this sample. There is no refresh or fallback path here.
    for part in value.get("details", {}).get("parts", {}).values():
        if part.get("status") in GATE_STATUS.values():
            return {"schema": value["schema"], "status": part["status"],
                    "observed_at": part["observed_at"], "data": None,
                    "error": part["error"]}
    return value
