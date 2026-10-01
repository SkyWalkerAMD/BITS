# 仓库目录约定

按共享核心、产品接入、文档、验证和兼容基线分类。0.3.0 将共用实现迁入 bits_core；完整系统使用固定的新目录配置，旧 OCRUN 由独立适配保留原路径与协议。

| 范围 | 目录 |
| --- | --- |
| 完整系统 | `distribution/`、`bits_core/center/` |
| 旧系统增强 | `legacy_plugin/`、`bits_core/results/` |
| 节点与结果 | `bits_core/batch/`、`bits_core/reporting/`、`bits_core/workloads/` |
| 接口与采集 | `sckocp_api/`、`bits_core/collector/`、`examples/` |
| 早期实现 | `ocrun/`；由历史入口单独使用，完整 BITS / bits-o 核心不导入它 |
| 验证与自动化 | `tests/`、`ci/`、`.github/workflows/`；组件测试保留在组件旁 |
| 上游基线 | `integrations/mon-sensors/`，保留原字节和哈希 |
| 私有研究 | 仅在独立私有归档，不进入公开 Git 历史 |
| 文档 | `docs/` 按部署、插件、节点、监控、报告、工具、安全、版本、开发、历史分类 |

## 迁移对照

| 原位置 | 当前位置 |
| --- | --- |
| mon_sensors_plugin / finish_addon | bits_core/collector / bits_core/batch |
| report_addon / control_addon | bits_core/reporting / bits_core/results |
| server_deploy / workload_suite | bits_core/center / bits_core/workloads |
| 根目录部署、监控、报告手册 | `docs/deployment/`、`docs/monitoring/`、`docs/reports/` |
| 组件 MANUAL / OPTIMIZATION | 对应的 `docs/` 分类 |
| FLEET / OPERATIONS / README-EXPERIMENTAL | `docs/archive/agent-0.12.8/` |
| 旧快速指南、旧验证结果 | `docs/archive/monitoring-0.3.1/`、`docs/archive/validation-20260926/` |
| `.cloud/` | `ci/`；临时 `.cloud-results/` 保持 Git 忽略 |
| 原生 sckocp 实现与研究 | 隔离到独立私有仓库 |
| server-release / workloads-release 旧入口 | 留在私有历史归档 |

根目录只保留项目说明、开发入口和兼容脚本。旧 Agent 安装脚本仍由历史运行包使用，不是当前部署入口。

## 产物与保留材料

安装包和证据在 Releases / Actions Artifacts，临时输出目录被 Git 忽略。`drafts/` 原样保留，不以整理为由删除客户数据、历史标签、许可证或未确认材料。

包内继续使用原 MANUAL.md、README.md、SCKOCP-API.md 等名称。迁移同步维护构建引用和客户源码清单，云端目录检查及实际构建验证遗漏。

本次核心迁移见[0.3.0 重构验收](../releases/0.3.0-VERIFICATION.md)；先前目录整理保留在[2026-09-30 整理验收](LAYOUT-20260930.md)。
