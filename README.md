# OCRUN：内网压测、监控与结果交付

本仓库当前主线使用 **原 OCRUN 任务协议**，整合跨发行版管理端、明确启动一次批次的节点执行器、sckocp 本地监控接口、报表与自动收尾，以及固定版本的压测工具库。

目标平台为 x86-64 的 Rocky/AlmaLinux 8–10、Debian 11–13、Ubuntu 22.04/24.04/26.04。RHEL 需要订阅与单独验收；容器验证不等于全部原生内核和硬件验收。

## 构建与下载

部署安装包在仓库 [Releases](https://github.com/SkyWalkerAMD/ocrun-next/releases) 下载。0.2.2 节点 RPM/DEB 直接包含 MLC 3.13、详细 HTML/JSON 验收报告，无需另外导入 MLC。管理端包同时携带两种节点安装包供内网分发。

旧系统使用独立的 **OCRUN 系统增强套件 `ocrun-plugin`**：分别安装控制端包和节点包，复用新版任务执行、工具库、监控、报告与恢复能力，保留原 `ws/occt` 菜单以及 215/217/211/221 服务。接入、操作与回滚见 [旧系统增强套件手册](legacy_plugin/MANUAL.md)，云端验证工作流为 **Original OCRUN system enhancement**。这与全新服务器的完整系统部署是两种安装方式。

在 GitHub **Actions → OCRUN integrated distribution** 中运行统一构建。工作流只在 Linux 云端构建，依次执行固定来源校验、RPM/DEB 编译、发行版安装测试、节点回归和中心服务验证，产物及证据也在该次运行的 **Artifacts** 中下载。

构建产物、源代码提交、工具版本、SHA-256 和测试记录使用同一份发布清单关联。不在 Windows 本机执行项目构建、测试或压测。

- [安装与发行说明](DISTRIBUTION.md)
- [工具版本、MLC 使用、SPEC 导入与回滚](workload_suite/MANUAL.md)
- [新中心安装及管理命令](server_deploy/MANUAL.md)
- [指定空闲节点验收](workload_suite/ACCEPTANCE.md)
- [节点运行、状态与异常恢复](finish_addon/OPTIMIZATION.md)
- [独立本地采集接口](SCKOCP-API.md)

## 整合的压测工具

| 工具 | 固定版本 | 说明 |
| --- | --- | --- |
| stress | 1.0.7 | 套件内置 |
| stress-ng | 0.22.01 | 套件内置 |
| P95 / mprime | 30.19 build 20 | 一份程序，m1/m2/m4；no/AVX/FMA3/AVX512 模式 |
| MLC | 3.13 | 套件内置官方 Linux 程序及原许可、文档 |
| MBW | 2.0 | 套件内置 |
| cyclictest | rt-tests 2.10 | 替换 sysjitter，实时延迟须实机验收 |
| UnixBench | 6.0.1 | 套件内置预编译程序 |
| SPEC CPU2017 | 原包 1.0.5 | 导入用户持有的原 0730，保留文件内容 |

上游来源、固定提交与归档校验值见 [工具锁定清单](workload_suite/sources.json)。MLC 原始归档保存在此私有仓库，安装包保留 Intel 许可、redist.txt 和使用文档；使用及后续分发仍受原许可约束。SPEC 沿用用户持有的原始材料。旧工具目录保留，未选择的工具不进入新中心任务目录。

## 运行边界

- 管理端保存任务，节点明确启动当前批次；不增加空闲持续领取任务。
- 节点调用本机已激活的原版 sckocp；不修改它的激活、离线规则，不开放监控网络 API。
- 任务执行、监控数据质量、报表和交付状态分别记录；失败恢复不自动重跑压测。
- 收尾产生 `.mon`、`.mon.sckocp.jsonl`、`.xlsx`、`.report.html`、`.report.json`、`.finish.json`，通过上传后下载副本核对 SHA-256；旧四文件回执仍可读取。
- sckocp v1 的传感器有效性和读数年龄保持“未知”，缺失字段为空，不推断硬件合格。
- 不触碰现有 215/217/211/221 服务器或其他生产节点；部署在明确选定的新中心和空闲节点验收。

## 旧实现与兼容入口

[早期 0.12.8 Agent 架构说明](README-EXPERIMENTAL.md)保留供历史维护。它使用另一套任务键空间并常驻领取任务，与当前原协议主线不混用；其根目录 `install-server.sh` / `install-client.sh` 不是本次统一发行的安装入口。

继续使用原管理端的已有节点，可按 [独立插件说明](MON-SENSORS.md) 接入；节点工具、采集与收尾代码与原 OCRUN 保持分离。

现有 215 原版控制端可以单独安装 [mon-sensors-control 查看插件](control_addon/MANUAL.md)：原 ws/occt 下发方式保持，通过原共享结果目录查看新采样、报表与收尾回执，按需核验文件 SHA-256。它是纯本地读取扩展，不使用新中心安装流程，也不修改原 OCRUN 程序。
