"""Create the standalone legacy integration artifact on the cloud runner."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build import (VERSION, PLUGIN_VERSION, PUBLIC_API_VERSION, metadata, mon_sensors_package,
                   sckocp_api_package, public_api_package)

destination = ROOT / ".cloud-results" / "mon-sensors"
destination.mkdir(parents=True, exist_ok=True)
archive = destination / ("mon-sensors-plugin-" + PLUGIN_VERSION + ".tar.gz")
mon_sensors_package(archive)
(destination / "manifest.json").write_text(json.dumps(metadata(archive), indent=2) + "\n")
api = destination / "api"
api.mkdir(exist_ok=True)
archive = api / ("ocrun-sckocp-api-" + VERSION + ".tar.gz")
sckocp_api_package(archive)
(api / "manifest.json").write_text(json.dumps(metadata(archive), indent=2) + "\n")
public = destination / "public-api"
public.mkdir(exist_ok=True)
archive = public / ("sckocp-api-" + PUBLIC_API_VERSION + ".tar.gz")
public_api_package(archive)
(public / "manifest.json").write_text(json.dumps(metadata(archive), indent=2) + "\n")
