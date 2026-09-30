"""Compatibility import; 0730 plugin installation lives outside OCRUN."""
import sys
from mon_sensors_plugin import install as _installer

if __name__ == "__main__":
    sys.exit(_installer.main())
else:
    sys.modules[__name__] = _installer
