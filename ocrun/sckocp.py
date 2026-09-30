"""Compatibility entrypoint; the shared provider now belongs to sckocp_api."""
import sys

from sckocp_api import provider as _provider

if __name__ == "__main__":
    sys.exit(_provider.main())
else:
    # Preserve the original module object, including validation helpers and
    # instrumentation hooks used by existing monitoring clients.
    sys.modules[__name__] = _provider
