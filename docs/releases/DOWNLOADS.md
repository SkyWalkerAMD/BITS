# 安装包下载索引

常规部署优先用 **RPM / DEB**。完整系统和旧系统增强套件各有管理/控制端、节点两种角色；同一台机器不要混装两套节点或同时装互斥角色。

| 用途 | EL 8 / 9 / 10：RPM | Debian 11 / 12 / 13、Ubuntu 22.04 / 24.04 / 26.04：DEB |
| --- | --- | --- |
| 新管理服务器 0.2.5 | [bits-center](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.2.5/bits-center-0.2.5-1.el8.x86_64.rpm) | [bits-center](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.2.5/bits-center_0.2.5-1_amd64.deb) |
| 新节点 0.2.5，已含工具 | [bits-node](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.2.5/bits-node-0.2.5-1.el8.x86_64.rpm) | [bits-node](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.2.5/bits-node_0.2.5-1_amd64.deb) |
| 旧 OCRUN 控制端增强 0.1.2 | [bits-o-control](https://github.com/SkyWalkerAMD/BITS/releases/download/bits-o-v0.1.2/bits-o-control-0.1.2-1.el8.x86_64.rpm) | [bits-o-control](https://github.com/SkyWalkerAMD/BITS/releases/download/bits-o-v0.1.2/bits-o-control_0.1.2-1_amd64.deb) |
| 旧 OCRUN 节点增强 0.1.2 | [bits-o-node](https://github.com/SkyWalkerAMD/BITS/releases/download/bits-o-v0.1.2/bits-o-node-0.1.2-1.el8.x86_64.rpm) | [bits-o-node](https://github.com/SkyWalkerAMD/BITS/releases/download/bits-o-v0.1.2/bits-o-node_0.1.2-1_amd64.deb) |
| 旧增强节点必需的工具库 0.1.0-3 | [bits-o-workloads](https://github.com/SkyWalkerAMD/BITS/releases/download/bits-o-v0.1.2/bits-o-workloads-0.1.0-3.el8.x86_64.rpm) | [bits-o-workloads](https://github.com/SkyWalkerAMD/BITS/releases/download/bits-o-v0.1.2/bits-o-workloads_0.1.0-3_amd64.deb) |

**完整系统不用另装 `bits-o-workloads`。** 管理端选择 `bits-center`；节点选择 `bits-node`，并单独安装、激活原版 `sckocp`。`bits-node` 已内含选定工具、本地接口、批次采集、报表和自动收尾；完整系统的工具通过这个 `bits-` 前缀的节点包交付。

完整系统 Release 中另附的 `bits-o-workloads` 供旧节点增强套件使用。它与 `bits-node` 包含相同工具文件，不能同时安装；不要把附件列表理解为每台机器都要安装的清单。历史 `mon-sensors-*.run` 和独立 API 安装器也不是完整节点的额外安装步骤。

这些包针对 x86-64。RPM 名称中的 `.el8` 表示兼容构建基线；实际云端矩阵包括 Rocky/AlmaLinux 8/9/10。RHEL、ARM、独立内核与真实硬件没有因此自动获得验收结论。

## 安装

先下载同一 Release 的 `SHA256SUMS` 核对所选文件，再在相应 Linux 机器以 root 安装，例如：

```bash
dnf install ./bits-node-0.2.5-1.el8.x86_64.rpm
```

或者：

```bash
apt install ./bits-node_0.2.5-1_amd64.deb
```

旧节点须在同一次包管理操作中提供 `bits-o-workloads` 和 `bits-o-node` 的同格式包。已经接入的旧安装先按手册停止、解除接入并保留备份，不用强制覆盖或降级。

安装只是安装文件，不等于配置完成或已开始压测。新部署按[完整系统手册](../deployment/DISTRIBUTION.md)配置；旧系统按[增强套件手册](../deployment/BITS.md)显式接入。旧控制机的 ws/occt、操作系统及已有任务保持原有使用方式。

## 独立组件

下列组件也在 [完整系统 0.2.5 的 Release](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.2.5) 直接提供，无需先下载整个大包：

| 组件 | 独立附件 | 适用情况 |
| --- | --- | --- |
| 本地接口 0.4.0 | `sckocp-api-0.4.0.run` | 第三方软件独立读取已激活 sckocp |
| 采集 0.13.0 | `mon-sensors-plugin-0.13.0.run` | 单独维护原采集接入 |
| 自动收尾 0.2.6 | `mon-sensors-finish-0.2.6.run` | 单独维护旧节点兼容组合 |
| Python 3.6 离线报表 0.2.0 | `mon-sensors-report-py36-0.2.0.run` | 旧节点缺少报表依赖 |
| 原控制端只读查看器 0.2.0 | `mon-sensors-control-0.2.0.run` | 仅需读取结果，不使用完整增强入口 |

完整节点 RPM/DEB 与旧节点增强套件已经集成私有批次采集和收尾，常规部署不需要再逐个安装这些 `.run` 文件，也不安装新版 mon-sensors 查看入口。上述 mon-sensors 命名附件仅保留历史独立组件的维护能力；独立 API 仍可单独安装。硬件状态直接用 sckocp 查看。

## 工具、许可与数据边界

工具库包括 stress 1.0.7、stress-ng 0.22.01、mprime 30.19b20（m1/m2/m4）、MLC 3.13、MBW 2.0、cyclictest 2.10、UnixBench 6.0.1。SPEC CPU2017 与原版 sckocp 不随公开包提供，使用方自行提供合法文件与激活。

MLC 的额外公开分发授权由发布方确认，仍受上游适用条款约束；见[发布说明](PUBLICATION.md)。公开仓库不将第三方二进制统一改授为 OCRUN 的许可。

已淘汰的旧安装包、现场测试压缩包、原生安全研究和同版本验证重建包保留在私有归档，不与当前可部署安装包混放。每个 Release 的清单记录全部直接附件；长期下载不依赖会过期的 Actions 临时产物。
