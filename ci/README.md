# Linux cloud fixtures

All project tests and builds run in disposable GitHub Actions Linux machines.
Some fixtures install services, generate test-only licenses or inject failures.
Never execute them on the operator workstation or production nodes.

See the [workflow map](../docs/development/CLOUD.md) and
[contribution guide](../CONTRIBUTING.md) before choosing a job.

| Files | Purpose |
| --- | --- |
| repository_layout.py, layout_packages.py | Repository links, preserved baselines, export boundary and native packaging regression |
| setup.sh, validate.sh, python36_smoke.py | Logic/process tests, shell checks and Python 3.6 compatibility |
| automatic_install_test.py, plugin_adoption_root_test.py, mon_sensors_legacy_test.py | API/plugin installers, adoption and original OCRUN integration |
| sckocp*_test.py, build_sckocp_patch.py | Private native fixtures with temporary signatures and synthetic hardware |
| api_security_matrix.py, api-security.Dockerfile | Independent API security and installation across distributions |
| server_*, server.Dockerfile | Original-protocol server services, containers and native-kernel VMs |
| installation.py, server_installation.py, integration*.py, distro_dependencies.sh | Earlier Agent installation and service recovery regressions |
| archive/ | Retired release entrypoints, preserved as disabled text |

Tests specific to current components remain beside those components. ci/ is
excluded from customer source exports. Generated files go in ignored result
directories, not this source directory.

The default Cloud Linux validation workflow runs eight enabled jobs. Its optional
eight-distribution dependency matrix concerns the earlier Agent; the complete
system and legacy enhancement have their own twelve-distribution acceptance.

A passing container job does not prove a distribution's native kernel or real
hardware behavior. Record the tested revision, actual run URL and limitations.
