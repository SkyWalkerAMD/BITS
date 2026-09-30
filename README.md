# OCRUN：内网压测、监控与结果交付

管理压测批次，在节点调用已激活的原版 sckocp，采集监控数据并生成 Excel、HTML 验收报告，上传后下载副本核对 SHA-256。

## 选择安装方式

| 场景 | 发布包 | 操作说明 |
| --- | --- | --- |
| 新服务器部署整套系统 | [OCRUN 0.2.3：管理端与节点 RPM/DEB](https://github.com/SkyWalkerAMD/ocrun-next/releases/tag/v0.2.3) | [完整系统部署](docs/deployment/DISTRIBUTION.md) |
| 旧 OCRUN 增加功能，保留 ws/occt | [ocrun-plugin 0.1.1：控制端与节点 RPM/DEB](https://github.com/SkyWalkerAMD/ocrun-next/releases/tag/ocrun-plugin-v0.1.1) | [旧系统增强套件](docs/plugins/OCRUN-PLUGIN.md) |
| 第三方程序读取本机监控 | sckocp-api 0.3.2，套件内另附独立安装器 | [接口操作手册](docs/monitoring/SCKOCP-API-操作手册.md) |

节点工具包括 stress 1.0.7、stress-ng 0.22.01、mprime 30.19b20（m1/m2/m4）、MLC 3.13、MBW 2.0、cyclictest 2.10、UnixBench 6.0.1。SPEC CPU2017 使用用户持有的原授权文件。[工具版本与命令](docs/workloads/MANUAL.md)。

已发布包的云端验收覆盖 x86-64 的 Rocky/AlmaLinux 8/9/10、Debian 11/12/13、Ubuntu 22.04/24.04/26.04。RHEL、其他架构和真实硬件需另行验收。[版本与验证记录](docs/releases/README.md)。

## 常用文档

- [全部文档导航](docs/README.md)：部署、插件、节点、监控、报表、工具、安全及历史。
- [节点任务、状态和失败恢复](docs/node/OPERATIONS.md)。
- [机器压测报告与交付文件](docs/reports/ACCEPTANCE-REPORT.md)。
- [升级与回滚](docs/releases/0.2.3.md)。
- [开发与云端验证](CONTRIBUTING.md)。

## 仓库地图

| 目录 | 用途 |
| --- | --- |
| `distribution/`、`server_deploy/` | 完整系统打包、新管理服务器部署 |
| `legacy_plugin/`、`control_addon/` | 旧系统增强入口、只读结果查看 |
| `finish_addon/`、`report_addon/`、`workload_suite/` | 节点执行与收尾、报表、固定版本工具 |
| `sckocp_api/`、`mon_sensors_plugin/` | 本地接口、采集与原 OCRUN 接入 |
| `ocrun/` | 共用兼容代码及早期 Agent；见目录说明 |
| `tests/`、`ci/`、`.github/workflows/` | 回归、Linux 云端夹具、自动化入口 |
| `integrations/` | 原 OCRUN 兼容基线 |
| `docs/` | 使用文档；历史材料在 `docs/archive/` |
| `research/` | 私有原生参考源码与安全候选，不属于客户交付 |

Python 导入路径和对外命令保持稳定；根目录构建、安装和命令入口用于兼容已有离线包。[目录约定与迁移对照](docs/development/REPOSITORY.md)。

## 使用边界

- 一次明确启动只处理当前批次，不增加空闲持续领任务。
- 保留原 sckocp 激活与离线规则，只用本地接口，不新增监控监听服务。
- 执行、质量、报告和交付状态分开记录；恢复不自动重跑压测。
- v1 未提供的有效性和读数年龄标为未知，缺失字段为空。
- 云端模拟传感器不等于硬件验收。构建和测试只在授权的 GitHub Actions Linux 执行。

客户交付使用 Releases 上传的安装包或白名单发行归档；不要分发旧源码附件或自动生成的 Source code。私有研究、凭据和本地 `drafts/` 不进入客户包。

[早期 Agent](docs/archive/agent-0.12.8/README.md) 使用另一套任务协议。根目录 `install-server.sh`、`install-client.sh` 是其兼容入口，不用于当前完整系统或旧系统套件部署。