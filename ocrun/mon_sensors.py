"""Compatibility import; implementation is maintained in bits_core.collector."""
import sys
from bits_core.collector import collector as _collector

if __name__ == "__main__":
    sys.exit(_collector.main())
else:
    sys.modules[__name__] = _collector
