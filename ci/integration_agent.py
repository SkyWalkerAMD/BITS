"""Cloud-only adapter: deterministic sensor fixture around the installed agent."""
import os
import time

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise SystemExit('This fixture is only for the disposable GitHub Actions runner')

import ocrun.agent as agent_module


class CloudCollector:
    def __init__(self, config=None):
        pass

    def sample(self, task_id=None):
        return {'task_id': task_id, 'timestamp': time.time(),
                'cpu_temperature_c': 40.0, 'cpu_avg_mhz': 2000.0,
                'cpu_busy_mhz': 2000.0, 'sensor_alerts': [],
                'collection_seconds': 0.0, 'cloud_simulated_sensor': True}

    def close(self):
        pass


agent_module.Collector = CloudCollector
agent_module.main()
