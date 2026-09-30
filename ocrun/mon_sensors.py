"""Compatibility import; implementation is maintained in mon_sensors_plugin."""
import sys
from mon_sensors_plugin import collector as _collector

if __name__ == "__main__":
    sys.exit(_collector.main())
else:
    sys.modules[__name__] = _collector
