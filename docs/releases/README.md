# 版本与验证记录

当前安装文件见 [RPM/DEB 下载索引](DOWNLOADS.md)；公开仓库的新历史、原测试提交与 Release 清单对应关系见[公开说明](PUBLICATION.md)。

已发布安装包保持不可变，后续目录整理不自动构成重新发版。

| 交付 | 版本 / 源码 | 云端证据 |
| --- | --- | --- |
| 完整系统 | [0.2.3](https://github.com/SkyWalkerAMD/ocrun-next/releases/tag/v0.2.3)，f56965c | [53 项作业通过](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36663201805)，12 系统；[发布附件核对](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36665377584) |
| 旧系统增强 | [0.1.1](https://github.com/SkyWalkerAMD/ocrun-next/releases/tag/ocrun-plugin-v0.1.1)，f56965c | [15 项作业通过](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36663205235)，12 系统及 Rocky 8 现场组件升级 |
| API / 采集 | 0.3.2 / 0.12.12 | [12 系统各 181 项通过](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36662869707)，c674d16 至 f56965c 对应运行代码无差异 |

完整系统首次 Ubuntu 24 作业因额度未启动，恢复后同提交补跑成功。模拟传感器不等于真实硬件、独立内核或 SELinux 现场验收。

[升级与回滚](0.2.3.md)。更早记录在[历史文档](../archive/README.md)，各自保留原日期与范围。[目录整理验收](../development/LAYOUT-20260930.md)单独记录，不替代上述发行证据。
