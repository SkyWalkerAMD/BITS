# 开发与维护

先阅读[仓库地图](docs/development/REPOSITORY.md)和[云端验证](docs/development/CLOUD.md)。修改前检查 Git 状态，保留已有修改和未跟踪文件；`drafts/` 留在本机，不提交、不自动删除。

## 文件归属

产品代码放对应组件；使用说明放 `docs/`；历史记录放 `docs/archive/`。云端夹具放 `ci/`，自动化入口留在 `.github/workflows/`。私有原生源码放 `research/`，原 OCRUN 兼容基线放 `integrations/mon-sensors/`。第三方工具固定来源、许可和哈希留在 `workload_suite/`，不加入重复二进制。

## 验证

项目构建、测试和压测只在私有 GitHub Actions Linux 执行。Windows 本机用于编辑、Git、下载和哈希核对，不执行项目构建或测试。

目录迁移运行 **Repository layout and packaging**，核对文档链接、客户源码边界、包内兼容文件名及 RPM/DEB 构建路径。代码变更按组件增加相应回归；硬件验收由用户在指定空闲机器执行。

## 发布

使用 **OCRUN integrated distribution** 或 **Original OCRUN system enhancement**。清单关联实际测试提交、版本和包哈希。目录整理不重写已有 tag 或替换 Release 附件；验证重建包不作为同版本正式更新分发。

客户源码严格按 `distribution/customer-sources.json` 导出。新生产文件需同步审阅；禁止整仓库归档、通配目录、链接、凭据及 `research/`、`ci/`、`drafts/` 进入客户包。调整文档位置时保留包内兼容文件名。

`ci/archive/` 是停用发布流程的追溯材料，不恢复成新的客户发行入口。
