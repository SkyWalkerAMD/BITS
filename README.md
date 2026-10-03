# BITS：内网压测、监控与结果交付

管理压测批次，在节点调用已激活的原版 sckocp，采集监控数据并生成 Excel、HTML 验收报告，上传后下载副本核对 SHA-256。

## 选择安装方式

**BITS 0.4.1 正式版**提供网页批次编排、持续硬件监控、BMC 凭据模板、单台 / 批量手动唤醒，以及报告和已结束批次的单条 / 批量永久删除。新部署使用 0.4.1；0.3.0 的旧协议安装和 BITS-o 保留各自手册。

| 场景 | 发布包 | 操作说明 |
| --- | --- | --- |
| 新部署或从 0.4.0 升级 | [BITS 0.4.1 正式版：管理端与节点 RPM/DEB](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.4.1) | [安装、升级与网页操作手册](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.1/docs/deployment/BITS-INDEPENDENT.md) |
| 维护已部署的 0.3 旧协议完整系统 | [BITS 0.3.0：管理端与节点 RPM/DEB](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.3.0) | [0.3 完整系统部署](docs/deployment/DISTRIBUTION.md) |
| 旧 OCRUN 增加功能，保留 ws/occt | [BITS-o 0.2.0：控制端与节点 RPM/DEB](https://github.com/SkyWalkerAMD/BITS/releases/tag/bits-o-v0.2.0) | [旧系统增强套件](docs/deployment/BITS.md) |
| 第三方程序读取本机监控 | [sckocp-api 0.4.0 独立安装器](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.3.0/sckocp-api-0.4.0.run) | [接口操作手册](docs/monitoring/SCKOCP-API-操作手册.md) |

## BITS 0.4 下载与网页操作

按机器角色和系统格式选择一个安装包，均为 x86-64：

| 机器角色 | Rocky / AlmaLinux 8–10：RPM | Debian 11–13、Ubuntu 22.04 / 24.04 / 26.04：DEB |
| --- | --- | --- |
| 管理服务器 | [bits-center RPM](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/bits-center-0.4.1-1.el8.x86_64.rpm) | [bits-center DEB](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/bits-center_0.4.1-1_amd64.deb) |
| 压测节点，已含工具 | [bits-node RPM](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/bits-node-0.4.1-1.el8.x86_64.rpm) | [bits-node DEB](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/bits-node_0.4.1-1_amd64.deb) |

[完整操作手册](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.1/docs/deployment/BITS-INDEPENDENT.md) · [离线手册下载](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/OPERATIONS.md) · [SHA256SUMS](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/SHA256SUMS) · [本版验证记录](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/VERIFICATION.json)

部署流程：

1. 在新中心安装 `bits-center`，按 **0.4 手册**选择现有 IP 和允许网段，配置 HTTPS、证书信任并启动服务。
2. 在网页登记节点并下载专属连接文件。节点安装 `bits-node`，导入连接文件后启动节点服务。节点包已包含工具及采集 / 报告适配，无需 `bits-o-workloads`；原版 sckocp 单独安装，授权在 BITS 之外完成。
3. 系统在线后，在节点卡片点击 **实时监控 ↗** 查看整机、插槽及逐核心读数，无需开始压测。需要测试时再编排批次、保存草稿并明确点击“开始”；结束后查看 HTML / Excel 报告及核验回执。空闲节点不自动领取其他批次。

在 **添加节点** 时直接选择自定义 BMC 凭据模板，也可在该窗口新建模板后自动选中。节点接入后发现本机管理口，中心按所选模板核对身份并绑定；账号密码不用逐台重填，也不下发节点。模板修改不覆盖已有绑定。支持 1–200 台网页任务组、明确批量开始、实时硬件监控和报告交付，三个页面统一显示关机可唤醒、等待系统与状态待确认。

本版可独立唤醒机器进入日常监控，支持多选和逐台连接进度；手动唤醒后保持开机，下一次明确开始压测后恢复原任务关机策略。报告和已结束批次支持选择、确认后永久删除中心记录及全部证据文件，页面显示清理进度。[0.4.1 更新说明](docs/releases/0.4.1.md)。

0.4 默认仅中心 HTTPS TCP 443 对节点开放，节点无需入站端口；使用本地 SQLite，不依赖 Redis / rsync。首次安装和系统服务操作仍在对应机器完成。从 0.4.0 升级需先完成批次并停止对应服务、完整备份，中心与节点均更新到 0.4.1。中心将数据库升级至 schema 4，保留现有配置与数据；回退需升级前完整快照。0.3 / OCRUN 使用不同协议，本版不提供它们的原地迁移。BMC 开机需中心访问管理口 UDP 623。

发布验证覆盖 12 个 Linux 容器环境、桌面 / 手机浏览器流程，以及 Rocky 8 RPM / Ubuntu 22 DEB 的 0.4.0 实包升级；结果随本版 VERIFICATION.json 提供。监控和 BMC 使用模拟数据 / 驱动，真实开机、硬件、各发行版原生内核、多天运行和 200 台物理节点需另行验收。[独立架构与 sckocp 权限边界](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.1/docs/development/BITS-INDEPENDENT.md)。

<details>
<summary>查看 0.4 实时工作台截图（云端模拟读数）</summary>

![BITS 0.4 实时工作台](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/dashboard.png)

[批量分发](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/dispatch-workspace.png) · [任务组结果](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/dispatch-group.png) · [运行详情](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/batch-running.png) · [任务编排](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/batch-wizard.png) · [手机布局](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/dashboard-mobile.png)

[硬件实时监控](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/hardware-monitor.png) · [逐核心表格](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/hardware-table.png) · [硬件监控手机版](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/hardware-mobile.png)

[空闲节点实时监控](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/hardware-idle.png) · [日常监控手机版](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/hardware-idle-mobile.png)

[滚动后的固定导航](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/hardware-scrolled.png) · [折叠侧栏](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/workspace-compact.png) · [手机导航菜单](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/navigation-mobile.png)

[总览关机可唤醒状态](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/bmc-overview.png) · [手机节点状态](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/bmc-nodes-mobile.png)（BMC 状态模拟）

[添加节点选择模板](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/node-template.png) · [手机添加节点](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.1/node-template-mobile.png)（示例配置）

</details>

## 工具与平台

节点工具包括 stress 1.0.7、stress-ng 0.22.01、mprime 30.19b20（m1/m2/m4）、MLC 3.13、MBW 2.0、cyclictest 2.10、UnixBench 6.0.1。SPEC CPU2017 使用用户持有的原授权文件。[工具版本与命令](docs/workloads/MANUAL.md)。

目标平台为 x86-64 的 Rocky/AlmaLinux 8/9/10、Debian 11/12/13、Ubuntu 22.04/24.04/26.04，实际验收明细随 Release 提供。**0.3 稳定版**管理端可用 `bits-center network` 查看现有地址、`bits-center setup --auto --check` 预检自动配置，保留现有网卡 IP、网关与 DNS；0.4 使用上方独立手册中的配置流程。RHEL、其他架构和真实硬件需另行验收。[版本与验证记录](docs/releases/README.md)。

## 常用文档

- [全部文档导航](docs/README.md)：部署、插件、节点、监控、报表、工具、安全及历史。
- [0.4 网页版：安装、节点接入、批次运行、报告与回退](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.1/docs/deployment/BITS-INDEPENDENT.md)。
- [0.3 稳定版任务、状态和失败恢复](docs/deployment/DISTRIBUTION.md)；[旧系统增强操作](docs/deployment/BITS.md)。
- [机器压测报告与交付文件](docs/reports/ACCEPTANCE-REPORT.md)。
- [0.3 迁移与回滚](docs/deployment/MIGRATION-0.3.md)。
- [开发与云端验证](CONTRIBUTING.md)。

## 仓库地图

| 目录 | 用途 |
| --- | --- |
| `native/` | 0.4 中心、节点、网页、安装包与云端验收 |
| `distribution/`、`bits_core/center/` | 0.3 旧协议完整系统打包与管理服务器部署；源码导出策略 |
| `legacy_plugin/`、`bits_core/results/` | 旧系统增强入口、只读结果查看 |
| `bits_core/batch/`、`bits_core/reporting/`、`bits_core/workloads/` | 节点执行与收尾、报表、固定版本工具 |
| `sckocp_api/`、`bits_core/collector/` | 本地接口、采集与原 OCRUN 接入 |
| `ocrun/` | 独立保留的早期 Agent；当前 BITS / bits-o 核心不导入它 |
| `tests/`、`ci/`、`.github/workflows/` | 回归、Linux 云端夹具、自动化入口 |
| `integrations/` | 原 OCRUN 兼容基线 |
| `docs/` | 使用文档；历史材料在 `docs/archive/` |
| 私有研究 | 原生 sckocp 源码和安全研究保留在独立私有仓库，不在公开历史中 |

0.3 完整系统使用统一的 bits_core 实现以及 `/opt/bits`、`/etc/bits`、`/var/lib/bits`、`/var/log/bits` 目录。旧 OCRUN 路径和挂钩集中在兼容适配中；历史协议、回执标识与原始数据保留。硬件查看使用 sckocp，批次内自动采集。[0.3 核心重构说明](docs/development/CORE-REFACTOR.md)。0.4 独立中心、网页及节点源码见 [v0.4.1 的 native 目录](https://github.com/SkyWalkerAMD/BITS/tree/v0.4.1/native)。

## 使用边界

- 一次明确启动只处理当前批次，不增加空闲持续领任务。
- 保留原 sckocp 激活与离线规则，只用本地接口，不新增监控监听服务。
- 执行、质量、报告和交付状态分开记录；恢复不自动重跑压测。
- v1 未提供的有效性和读数年龄标为未知，缺失字段为空。
- 云端模拟传感器不等于硬件验收。构建和测试只在授权的 GitHub Actions Linux 执行。

优先从 [RPM/DEB 下载索引](docs/releases/DOWNLOADS.md) 选择安装包。公开仓库使用新的、经过审阅的提交历史；原仓库及旧附件留在私有归档。当前自动生成的 Source code 只含公开快照，安装请使用 Release 的 RPM/DEB。详见[公开范围与版本追溯](docs/releases/PUBLICATION.md)。

[早期 Agent](docs/archive/agent-0.12.8/README.md) 使用另一套任务协议。根目录 `install-server.sh`、`install-client.sh` 是其兼容入口，不用于当前完整系统或旧系统套件部署。
