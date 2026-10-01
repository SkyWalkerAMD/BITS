# 开发与维护

先阅读[仓库地图](docs/development/REPOSITORY.md)和[云端验证](docs/development/CLOUD.md)。修改前检查 Git 状态，保留已有修改和未跟踪文件；`drafts/` 留在本机，不提交、不自动删除。

## 文件归属

产品代码放对应组件；使用说明放 `docs/`；历史记录放 `docs/archive/`。云端夹具放 `ci/`，自动化入口留在 `.github/workflows/`。私有原生源码仅留在独立私有仓库，禁止合入此公开仓库；原 OCRUN 兼容基线放 `integrations/mon-sensors/`。第三方工具固定来源、许可和哈希留在 `bits_core/workloads/`，不加入重复二进制。

独立架构预览集中在 `native/`，其隔离验收夹具位于 `native/ci/`。它与稳定版及 BITS-o 分别构建，不能把预览通过误写为旧生产系统已经迁移。

## 验证

项目构建、测试和压测只在私有 GitHub Actions Linux 执行。Windows 本机用于编辑、Git、下载和哈希核对，不执行项目构建或测试。

目录迁移运行 **Repository layout and packaging**，核对文档链接、客户源码边界、包内兼容文件名及 RPM/DEB 构建路径。代码变更按组件增加相应回归；硬件验收由用户在指定空闲机器执行。

## 发布

使用 **BITS integrated distribution** 或 **BITS-o legacy enhancement packages**。清单关联实际测试提交、版本和包哈希。目录整理不重写已有 tag 或替换 Release 附件；验证重建包不作为同版本正式更新分发。

独立架构使用 **Independent BITS architecture**，完整矩阵成功后生成单独的预发布附件。此产品只安装中心、节点两个角色包，所需本地适配和工具已在节点包内；不套用下述旧发行版七个独立组件的附件要求。预览使用新 tag 和 prerelease，不替换稳定版 Latest。

客户源码严格按 `distribution/customer-sources.json` 导出。新生产文件需同步审阅；禁止整仓库归档、通配目录、链接、凭据及 `research/`、`ci/`、`drafts/` 进入客户包。调整文档位置时保留包内兼容文件名。

依赖旧提交的完整矩阵、历史兼容夹具和原生安全研究继续在私有归档执行；公开镜像的 Actions 默认关闭。不得通过合并旧分支把原生实现重新带入公开历史。发布前运行附件清单检查，完整系统的七个独立组件都必须出现在 Release 下载列表。
