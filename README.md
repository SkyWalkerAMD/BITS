# BITS：内网压测、监控与结果交付

管理压测批次，在节点调用已激活的原版 sckocp，采集监控数据并生成 Excel、HTML 验收报告，上传后下载副本核对 SHA-256。

## 选择安装方式

**BITS 0.4 网页版已发布预览包，当前版本为 `0.4.0-alpha.5`。** 它提供独立中心、节点实时状态、网页批次编排和报告下载，适合新建隔离测试环境。稳定版仍为 0.3.0；旧 OCRUN 使用 BITS-o。

| 场景 | 发布包 | 操作说明 |
| --- | --- | --- |
| 新服务器体验独立架构、实时网页管理（预览版） | [BITS 0.4.0-alpha.5：管理端与节点 RPM/DEB](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.4.0-alpha.5) | [0.4 安装与网页操作手册](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.0-alpha.5/docs/deployment/BITS-INDEPENDENT-PREVIEW.md) |
| 部署现有稳定版完整系统 | [BITS 0.3.0：管理端与节点 RPM/DEB](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.3.0) | [0.3 完整系统部署](docs/deployment/DISTRIBUTION.md) |
| 旧 OCRUN 增加功能，保留 ws/occt | [BITS-o 0.2.0：控制端与节点 RPM/DEB](https://github.com/SkyWalkerAMD/BITS/releases/tag/bits-o-v0.2.0) | [旧系统增强套件](docs/deployment/BITS.md) |
| 第三方程序读取本机监控 | [sckocp-api 0.4.0 独立安装器](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.3.0/sckocp-api-0.4.0.run) | [接口操作手册](docs/monitoring/SCKOCP-API-操作手册.md) |

## BITS 0.4 下载与网页操作

按机器角色和系统格式选择一个安装包，均为 x86-64：

| 机器角色 | Rocky / AlmaLinux 8–10：RPM | Debian 11–13、Ubuntu 22.04 / 24.04 / 26.04：DEB |
| --- | --- | --- |
| 管理服务器 | [bits-center RPM](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/bits-center-0.4.0-0.alpha.5.el8.x86_64.rpm) | [bits-center DEB](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/bits-center_0.4.0.alpha.5-1_amd64.deb) |
| 压测节点，已含工具 | [bits-node RPM](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/bits-node-0.4.0-0.alpha.5.el8.x86_64.rpm) | [bits-node DEB](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/bits-node_0.4.0.alpha.5-1_amd64.deb) |

[完整操作手册](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.0-alpha.5/docs/deployment/BITS-INDEPENDENT-PREVIEW.md) · [离线手册下载](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/OPERATIONS.md) · [SHA256SUMS](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/SHA256SUMS) · [本版验证记录](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/VERIFICATION.json)

部署流程：

1. 在新中心安装 `bits-center`，按 **0.4 手册**选择现有 IP 和允许网段，配置 HTTPS、证书信任并启动服务。
2. 在网页登记节点并下载专属连接文件。节点安装 `bits-node`，导入连接文件后启动节点服务。节点包已包含工具及采集 / 报告适配，无需 `bits-o-workloads`；原版 sckocp 单独安装，授权在 BITS 之外完成。
3. 网页编排批次、保存草稿，核对后明确点击“开始”。在运行节点卡片点击 **实时监控 ↗**，查看整机指标、插槽概况及按数字排序的核心矩阵 / 表格；结束后查看 HTML / Excel 报告及核验回执。空闲节点不自动领取其他批次。

alpha.5 新增网页任务组：一次选择 1–200 台可达节点、保存共用模板、明确批量开始、逐台跟进结果。绑定 BMC 后，关机但管理口可达的节点可按需开机，系统连接后再执行；不可达节点不能选择。保留固定导航、实时硬件监控与报告交付。

0.4 默认仅中心 HTTPS TCP 443 对节点开放，节点无需入站端口；使用本地 SQLite，不依赖 Redis / rsync。首次安装和系统服务操作仍在对应机器完成。0.3 的自动网络配置、升级或迁移命令不能直接套用到 0.4；当前预览版不提供生产系统原地迁移。可选 BMC 开机功能另需中心访问管理口 UDP 623。

本版已通过 12 个 Linux 容器环境及桌面 / 手机浏览器流程验证，监控使用模拟读数。真实 BMC 开机、硬件、各发行版原生内核、多天运行和 200 台物理节点仍待另行验收。[独立架构与 sckocp 权限边界](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.0-alpha.5/docs/development/BITS-INDEPENDENT.md)。

<details>
<summary>查看 0.4 实时工作台截图（云端模拟读数）</summary>

![BITS 0.4 实时工作台](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/dashboard.png)

[批量分发](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/dispatch-workspace.png) · [任务组结果](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/dispatch-group.png) · [运行详情](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/batch-running.png) · [任务编排](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/batch-wizard.png) · [手机布局](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/dashboard-mobile.png)

[硬件实时监控](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/hardware-monitor.png) · [逐核心表格](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/hardware-table.png) · [硬件监控手机版](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/hardware-mobile.png)

[滚动后的固定导航](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/hardware-scrolled.png) · [折叠侧栏](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/workspace-compact.png) · [手机导航菜单](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/navigation-mobile.png)

</details>

## 工具与平台

节点工具包括 stress 1.0.7、stress-ng 0.22.01、mprime 30.19b20（m1/m2/m4）、MLC 3.13、MBW 2.0、cyclictest 2.10、UnixBench 6.0.1。SPEC CPU2017 使用用户持有的原授权文件。[工具版本与命令](docs/workloads/MANUAL.md)。

目标平台为 x86-64 的 Rocky/AlmaLinux 8/9/10、Debian 11/12/13、Ubuntu 22.04/24.04/26.04，实际验收明细随 Release 提供。**0.3 稳定版**管理端可用 `bits-center network` 查看现有地址、`bits-center setup --auto --check` 预检自动配置，保留现有网卡 IP、网关与 DNS；0.4 使用上方独立手册中的配置流程。RHEL、其他架构和真实硬件需另行验收。[版本与验证记录](docs/releases/README.md)。

## 常用文档

- [全部文档导航](docs/README.md)：部署、插件、节点、监控、报表、工具、安全及历史。
- [0.4 网页版：安装、节点接入、批次运行、报告与回退](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.0-alpha.5/docs/deployment/BITS-INDEPENDENT-PREVIEW.md)。
- [0.3 稳定版任务、状态和失败恢复](docs/deployment/DISTRIBUTION.md)；[旧系统增强操作](docs/deployment/BITS.md)。
- [机器压测报告与交付文件](docs/reports/ACCEPTANCE-REPORT.md)。
- [0.3 迁移与回滚](docs/deployment/MIGRATION-0.3.md)。
- [开发与云端验证](CONTRIBUTING.md)。

## 仓库地图

| 目录 | 用途 |
| --- | --- |
| `distribution/`、`bits_core/center/` | 完整系统打包、新管理服务器部署 |
| `legacy_plugin/`、`bits_core/results/` | 旧系统增强入口、只读结果查看 |
| `bits_core/batch/`、`bits_core/reporting/`、`bits_core/workloads/` | 节点执行与收尾、报表、固定版本工具 |
| `sckocp_api/`、`bits_core/collector/` | 本地接口、采集与原 OCRUN 接入 |
| `ocrun/` | 独立保留的早期 Agent；当前 BITS / bits-o 核心不导入它 |
| `tests/`、`ci/`、`.github/workflows/` | 回归、Linux 云端夹具、自动化入口 |
| `integrations/` | 原 OCRUN 兼容基线 |
| `docs/` | 使用文档；历史材料在 `docs/archive/` |
| 私有研究 | 原生 sckocp 源码和安全研究保留在独立私有仓库，不在公开历史中 |

0.3 完整系统使用统一的 bits_core 实现以及 `/opt/bits`、`/etc/bits`、`/var/lib/bits`、`/var/log/bits` 目录。旧 OCRUN 路径和挂钩集中在兼容适配中；历史协议、回执标识与原始数据保留。硬件查看使用 sckocp，批次内自动采集。[0.3 核心重构说明](docs/development/CORE-REFACTOR.md)。0.4 独立中心、网页及节点源码见 [v0.4.0-alpha.5 的 native 目录](https://github.com/SkyWalkerAMD/BITS/tree/v0.4.0-alpha.5/native)。

## 使用边界

- 一次明确启动只处理当前批次，不增加空闲持续领任务。
- 保留原 sckocp 激活与离线规则，只用本地接口，不新增监控监听服务。
- 执行、质量、报告和交付状态分开记录；恢复不自动重跑压测。
- v1 未提供的有效性和读数年龄标为未知，缺失字段为空。
- 云端模拟传感器不等于硬件验收。构建和测试只在授权的 GitHub Actions Linux 执行。

优先从 [RPM/DEB 下载索引](docs/releases/DOWNLOADS.md) 选择安装包。公开仓库使用新的、经过审阅的提交历史；原仓库及旧附件留在私有归档。当前自动生成的 Source code 只含公开快照，安装请使用 Release 的 RPM/DEB。详见[公开范围与版本追溯](docs/releases/PUBLICATION.md)。

[早期 Agent](docs/archive/agent-0.12.8/README.md) 使用另一套任务协议。根目录 `install-server.sh`、`install-client.sh` 是其兼容入口，不用于当前完整系统或旧系统套件部署。
