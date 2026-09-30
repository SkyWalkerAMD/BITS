# 公开仓库与制品追溯

2026-09-30，维护者选择隔离私有实现后公开 OCRUN：

- `SkyWalkerAMD/ocrun-next` 使用经过审阅的新 Git 历史，包含 OCRUN、独立本地接口及插件的交付源码。
- 原仓库另存为私有的 `SkyWalkerAMD/ocrun-next-private-archive`，保留原提交、PR、旧 Release、Actions 验证及 sckocp 原生源码和授权研究。
- 公开仓库不是旧仓库的 fork，不导入旧分支或 PR 引用。不要向公开仓库合并旧私有历史。
- 本机原工作目录及 drafts 保留。公开工作副本与私有工作副本使用不同远端。

这解决交付中不必要的 sckocp 实现暴露；Python 接口仍然可以阅读，不能据此承诺抵抗完全控制设备的 root 或绝对防逆向。

## 已验证安装包

完整系统 0.2.3 与旧增强套件 0.1.1 的安装包仍来自原测试提交 `f56965cb22eb592a934b74e13fae7637fb5c2783`。公开迁移不重编译、不替换既有包字节；原 `RELEASE.json`、`VALIDATION.json` 和安装包中的提交号保留原值。

公开版本标签使用经过白名单导出的对应源码快照，提交号会与私有原提交不同。`PUBLICATION.json` 记录映射；`SOURCE-MANIFEST.json` 和客户源码归档记录每个文件的哈希。不要把公开快照的新提交号误报为原安装包的构建提交。

| 原验收 | 范围 |
| --- | --- |
| [36663201805](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36663201805) | 完整系统 53 项作业；12 系统的安装、认证任务、交付与升级回滚 |
| [36663205235](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36663205235) | 旧系统增强 15 项作业；12 系统和 Rocky 8 现场组合升级模拟 |
| [36662869707](https://github.com/SkyWalkerAMD/ocrun-next-private-archive/actions/runs/36662869707) | 独立 API 安全与安装回归；12 系统，各 181 项测试 |

历史运行链接需要私有仓库权限；公开发行归档同时保留原验证记录。硬件、SELinux 独立内核、长期满负载、MLC 完整硬件工作负载和 SPEC 全项仍需现场验收。源码和附件清单审计不替代上述验收。

## 附件补齐与防遗漏

发布选择器现在要求完整系统的管理端/节点 RPM/DEB、七个独立组件文件、源码、部署/接口/报告手册及报告样例。文件必须出现在原发行清单中且 SHA-256 一致；新增未知组件或缺失已知组件会报错，不能默默省略。

公开 Release 的 `SHA256SUMS` 覆盖全部直接附件。原内嵌清单和已有历史校验文件保持原值，来源关系写入 `PUBLICATION.json`。校验值可检测文件变化，尚不是发布者数字签名。

## MLC 3.13

维护者于 2026-09-30 明确确认具有覆盖 MLC 3.13 公开分发的额外授权，本次继续整合该版本。此记录不公开授权合同、不转授额外权利，也不把 MLC 声称为 GPL 工具。

原 Intel 压缩包 SHA-256 为 `a8537e8ff3fad626d75a383fabc224ccc4cc98a0111c9989f7fb26b639f12019`。其中名为许可 PDF 的成员实际上是另一份 gzip/tar 文件，`redist.txt` 中写的是 `mlc_internal`。现有已验收安装包保留原字节，另附正确的官方许可链接和本说明，不声称原成员已经是正确的许可 PDF。

使用及再分发遵守适用授权：[Intel 官方下载与许可](https://www.intel.com/content/www/us/en/download/736633/intel-memory-latency-checker-intel-mlc.html)、[官方许可 PDF](https://cdrdv2-public.intel.com/736369/Intel%20Memory%20Latency%20Tools%20Outbound%20License%20Agreement%20FINAL.pdf)。第三方版权、通知和开源源码仍随原发行归档保留。

## 后续维护

按维护者要求，构建和测试继续在私有 GitHub Actions Linux 执行，公开镜像的 Actions 默认关闭。含历史提交依赖的完整工作流仅在私有归档中运行。公开代码的修订需同步私有验证分支，测试后只发布审阅过的源码和制品，不能把私有 Git 对象、临时签名材料或研究产物推回公开仓库。
