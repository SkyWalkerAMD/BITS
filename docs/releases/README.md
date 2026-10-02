# 版本与验证记录

当前安装文件见 [RPM/DEB 下载索引](DOWNLOADS.md)；公开仓库的新历史、原测试提交与 Release 清单对应关系见[公开说明](PUBLICATION.md)。

已发布安装包保持不可变，后续目录整理不自动构成重新发版。

| 交付 | 版本 / 源码 | 云端证据 |
| --- | --- | --- |
| BITS 独立架构：自动 BMC 绑定与统一状态（预览） | [0.4.0-alpha.6](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.4.0-alpha.6)；[操作与升级](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.0-alpha.6/docs/deployment/BITS-INDEPENDENT-PREVIEW.md) | 同版 [VERIFICATION.json](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.6/VERIFICATION.json) 记录源码与 Linux / 浏览器检查。自动绑定使用模拟 BMC 核对身份、失败持久化和凭据隔离；总览与节点页区分 BMC 可达和系统在线。物理 BMC 自动发现及唤醒需现场验收 |
| BITS 独立架构：批量分发（预览） | [0.4.0-alpha.5](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.4.0-alpha.5)；[操作手册](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.0-alpha.5/docs/deployment/BITS-INDEPENDENT-PREVIEW.md) | [VERIFICATION.json](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.5/VERIFICATION.json)；任务组与批量开始、BMC 开机等待、取消及逐台交付的 Linux 和浏览器验收。模拟 BMC 不代替物理开机验收 |
| BITS 独立架构：网页操作改进（预览） | [0.4.0-alpha.4](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.4.0-alpha.4)；[操作手册](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.0-alpha.4/docs/deployment/BITS-INDEPENDENT-PREVIEW.md) | 本版 [VERIFICATION.json](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.4/VERIFICATION.json) 对应实际源码和 Linux / 浏览器检查；固定导航、抽屉菜单、位置保留、分页与提交保护均有交互验收。模拟读数不代替真实硬件验收 |
| BITS 独立架构：逐节点硬件实况（预览） | [0.4.0-alpha.3](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.4.0-alpha.3)；[安装与网页操作](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.0-alpha.3/docs/deployment/BITS-INDEPENDENT-PREVIEW.md) | 源码提交、Linux 安装与浏览器检查以本版 [VERIFICATION.json](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.3/VERIFICATION.json) 为准；新增插槽 / 核心投影、数值排序、分页面板及失联提示，模拟数据不代替真实硬件验收 |
| BITS 独立架构：实时网页工作台（预览） | [0.4.0-alpha.2](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.4.0-alpha.2)，bb05159；[安装与网页操作](https://github.com/SkyWalkerAMD/BITS/blob/v0.4.0-alpha.2/docs/deployment/BITS-INDEPENDENT-PREVIEW.md) | [公开验证清单](https://github.com/SkyWalkerAMD/BITS/releases/download/v0.4.0-alpha.2/VERIFICATION.json)：12 个系统用户空间、14 项 Go 测试及 race 检查、Rocky 8 / Ubuntu 22 各 52 项接口检查、36 次中断恢复和 11 组浏览器操作检查；模拟读数，真实硬件与长任务另行验收 |
| BITS 完整系统：统一核心 | [0.3.0](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.3.0)，a7dda66 | [53 项作业通过](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36810888642)，12 系统及真实 0.2.5 包的历史导入/回滚；见[验收记录](0.3.0-VERIFICATION.md) |
| BITS-o 旧系统适配：共享核心 | [0.2.0](https://github.com/SkyWalkerAMD/BITS/releases/tag/bits-o-v0.2.0)，a7dda66 | [15 项作业通过](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36811505900)，12 系统、原 occt 协议及现场组件升级；硬件验收单独进行 |
| BITS 完整系统：自动网络配置 | [0.2.5](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.2.5)，9579220 | [本轮 24 项作业通过](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36800108426)，12 系统；未变更组件复用已核验运行，见 [记录](0.2.5.md) |
| BITS 完整系统 | [0.2.4](https://github.com/SkyWalkerAMD/BITS/releases/tag/v0.2.4) | 同版 RELEASE.json 记录测试提交、12 系统矩阵和私有云端运行；新增硬件字段待现场验收 |
| BITS-o 旧系统增强 | [0.1.2](https://github.com/SkyWalkerAMD/BITS/releases/tag/bits-o-v0.1.2) | 同版 VALIDATION.json 包含原 occt 协议、安装迁移及回滚验证；不用替换原菜单 |
| 完整系统 | [0.2.3](https://github.com/SkyWalkerAMD/ocrun-next/releases/tag/v0.2.3)，f56965c | [53 项作业通过](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36663201805)，12 系统；[发布附件核对](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36665377584) |
| 旧系统增强 | [0.1.1](https://github.com/SkyWalkerAMD/ocrun-next/releases/tag/ocrun-plugin-v0.1.1)，f56965c | [15 项作业通过](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36663205235)，12 系统及 Rocky 8 现场组件升级 |
| API / 采集 | 0.3.2 / 0.12.12 | [12 系统各 181 项通过](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36662869707)，c674d16 至 f56965c 对应运行代码无差异 |

完整系统首次 Ubuntu 24 作业因额度未启动，恢复后同提交补跑成功。模拟传感器不等于真实硬件、独立内核或 SELinux 现场验收。

[BITS 升级与回滚](0.2.5.md)。[BITS 0.2.4 说明](0.2.4.md)、[原 0.2.3 说明](0.2.3.md)及[历史文档](../archive/README.md)各自保留原日期与范围。[目录整理验收](../development/LAYOUT-20260930.md)单独记录，不替代发行证据。
