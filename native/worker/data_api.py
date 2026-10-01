"""The only sckocp invocation surface used by independent BITS.

This module exposes sampling, not a command runner. Enrollment, batch input and
network requests cannot set an executable, operation, environment or arguments.
The native program remains solely responsible for authorization.
"""
import shutil

from sckocp_api.interface import collect


def sample(include_details=False):
    if type(include_details) is not bool:
        raise ValueError("Sampling accepts only the supplemental-data flag")
    binary = shutil.which("sckocp", path="/usr/local/bin:/usr/bin")
    if binary is None:
        raise ValueError("Original sckocp data provider is unavailable")
    # collect accepts only mon JSON and fixed mon/info supplements. Its output
    # whitelist removes non-Primary timing groups before they reach BITS.
    return collect(binary=binary, interval=1, timeout=20,
                   native_format="v1", details=include_details)
