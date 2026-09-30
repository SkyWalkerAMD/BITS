# Resource OCRUN 0.9.24a fixtures

These four unmodified text files were supplied in the resource-server core archive on 2026-09-26. Their SHA-256 hashes match the four files reported on K6C-183. They are test input, not executable instructions for the developer workstation.

The fixture excludes oc.env, credentials, fbin, activation material and other resource-server files. Tests patch copies in disposable Linux directories and supply synthetic environments. The authoritative full-release allowlist is in mon_sensors_plugin/adoption.py; it matches bytes, not a version label.
