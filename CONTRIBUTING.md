# 开发与维护

先阅读[仓库地图](docs/development/REPOSITORY.md)和[云端验证](docs/development/CLOUD.md)。修改前检查 Git 状态，保留已有修改和未跟踪文件；`drafts/` 留在本机，不提交、不自动删除。

## 文件归属

产品代码放对应组件；使用说明放 `docs/`；历史记录放 `docs/archive/`。云端夹具放 `ci/`，自动化入口留在 `.github/workflows/`。私有原生源码仅留在独立私有仓库，禁止合入此公开仓库；原 OCRUN 兼容基线放 `integrations/mon-sensors/`。第三方工具固定来源、许可和哈希留在 `bits_core/workloads/`，不加入重复二进制。

BITS 0.4 独立架构集中在 `native/`，其隔离验收夹具位于 `native/ci/`。它与 0.3 旧协议及 BITS-o 分别构建，云端通过不代表旧生产系统已经迁移。

## 验证

项目构建、测试和压测只在私有 GitHub Actions Linux 执行。Windows 本机用于编辑、Git、下载和哈希核对，不执行项目构建或测试。

目录迁移运行 **Repository layout and packaging**，核对文档链接、客户源码边界、包内兼容文件名及 RPM/DEB 构建路径。代码变更按组件增加相应回归；硬件验收由用户在指定空闲机器执行。

## 发布

0.3 旧协议使用 **BITS integrated distribution**，BITS-o 使用 **BITS-o legacy enhancement packages**。清单关联实际测试提交、版本和包哈希。目录整理不重写已有 tag 或替换 Release 附件；验证重建包不作为同版本正式更新分发。

0.4 独立架构使用 **Independent BITS architecture**，完整矩阵、升级与浏览器检查成功后生成同次运行附件。此产品只安装中心、节点两个角色包，所需本地适配和工具已在节点包内。0.4.0 按维护者要求发布正式版，使用不可变新 tag、非 prerelease 并更新 Latest；保留旧版本附件。发布前核对四个 RPM/DEB、审核源码包、手册、验证记录与 SHA256SUMS，源码须对应实际测试提交。

客户源码严格按 `distribution/customer-sources.json` 导出。新生产文件需同步审阅；禁止整仓库归档、通配目录、链接、凭据及 `research/`、`ci/`、`drafts/` 进入客户包。调整文档位置时保留包内兼容文件名。

依赖旧提交的完整矩阵、历史兼容夹具和原生安全研究继续在私有归档执行；公开镜像的 Actions 默认关闭。不得通过合并旧分支把原生实现重新带入公开历史。旧协议完整系统的七个独立组件必须出现在该类 Release 下载列表；这一要求不适用于 0.4 两角色发行版。
