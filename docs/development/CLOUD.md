# 云端验证与构建

项目测试、构建和安装验证只在私有 GitHub Actions 的临时 Linux 环境运行。Windows 用于编辑、Git、下载及文件校验。历史命令、夹具和故障注入不在生产机重放。

## 按修改范围选择入口

| 修改范围 | Actions 工作流 | 用途 |
| --- | --- | --- |
| 目录、文档、构建输入 | Repository layout and packaging | 文档链接、基线文件、源码导出边界；真实构建完整系统和旧系统 RPM/DEB |
| 接口、采集、兼容安装 | Cloud Linux validation | 公开源码保留 6 项兼容回归；原生 1.1/1.2 模拟仅在私有归档 |
| 接口安全 | Independent API security regression | 12 系统的接口安装、升级及安全回归 |
| 0.3 旧协议完整系统 | BITS integrated distribution | 固定工具、服务器服务、原生安装包、12 系统流程及发行归档 |
| 独立架构 0.4 | Independent BITS architecture | Go 协议/并发、数据权限边界、真实 systemd 与短任务、网页及 RPM/DEB；full_matrix 验证 12 系统与 alpha.7 实包升级，汇总正式版附件 |
| 旧系统增强 | BITS-o legacy enhancement packages | 默认 Rocky 8 / Ubuntu 22；full_matrix 扩展到 12 系统并汇总归档 |
| 节点执行及报表 | Offline node task finalization / Offline report supplement | Python 3.6 离线安装、失败恢复、旧节点升级与报表 |
| 单独组件 | Original controller read-only plugin / Original-protocol server deployment matrix / Version-pinned workload RPM and DEB | 对应组件构建和验收 |
| 原生安全研究 | 私有仓库专用 | 不在公开仓库提供实现、签名夹具或编译产物 |
| 独立内核、报告版式 | Server native-kernel VM acceptance / Acceptance report print review | 按具体参数执行，不能用容器结果替代 |
| 发布附件 | Publish verified distribution draft | 仅汇总已验证的源码和附件；人工检查后发布 |

在[私有验证仓库的 Actions](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions) 选择工作流和目标分支；公开 BITS 镜像的 Actions 保持关闭。结果必须记录源码提交、实际运行链接与范围；未启动、跳过和阻塞均不能算通过。目录整理重建的同版本包只用于回归，不覆盖现有 Release。

Cloud Linux validation 的可选 distro_matrix 是早期 Agent 的 8 系统依赖检查，包含 CentOS 7.9 和 Ubuntu 20.04；**它不是当前完整系统 12 系统矩阵**。当前完整系统与旧插件针对 x86-64 Rocky/AlmaLinux 8/9/10、Debian 11/12/13、Ubuntu 22.04/24.04/26.04。

## 证据和边界

- [发行版验证记录](../releases/README.md)关联不可变的发布版本。更早材料在[历史目录](../archive/README.md)。
- 工作流附件保留期由各自配置决定；长期交付的包、清单和证据放 Releases。
- ci/ 内测试可能安装服务、签署临时测试租约或注入故障，只用于隔离 Linux。夹具详情见维护者仓库 [ci/README.md](https://github.com/SkyWalkerAMD/ocrun-next/blob/main/ci/README.md)。
- 容器共享宿主内核；模拟传感器不证明真实硬件、SELinux 独立内核、长期压力或授权服务的现场行为。
- 真实硬件验收只提供具体命令，由用户在指定空闲节点执行；不自动操作其他节点。
- 云端使用临时凭据。原生研究及生产秘密不进入客户包；客户源码只按审核清单导出。
