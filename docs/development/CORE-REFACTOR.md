# BITS 核心重构 0.3.0

本次将执行、采集、报表、结果读取、管理端和工具库的实现归入 `bits_core/`。
`distribution/` 负责完整系统的安装和操作入口，`legacy_plugin/` 负责旧 OCRUN 的接入。
两种产品使用同一份核心实现，旧 OCRUN 的原始脚本继续保留在 `integrations/`，不修改其基线。

| 内容 | 完整 BITS | 旧 OCRUN 兼容版 |
|---|---|---|
| 安装入口 | bits-center / bits-node | bits-o |
| 程序 | /opt/bits/center、/opt/bits/node | 旧应用之外的独立增强包 |
| 节点配置 | /etc/bits/node | 原兼容配置 |
| 节点运行状态 | /var/lib/bits/node/app/state | 原应用的 .mon-sensors-finish |
| 节点日志 | /var/log/bits/node | 原 /root/log |
| 工具库 | /opt/bits/workloads | 原独立兼容工具库 |
| 服务端结果 | /srv/bits/results | 原共享目录 |

目录配置由 `bits_core/layout.py` 定义，打包时产生固定的 `bits_layout.py`。
它与其他程序一并纳入哈希清单，不读取环境变量或可写配置来选择实现。
完整节点只允许已绑定的 BITS 工具库；绑定缺失时停止，不回退到旧工具路径。
原 `ocb`、`oct` 挂钩只属于兼容接入，完整节点使用独立的 scheduler、batch、collect 入口。
collect 是批次内部程序，查看硬件信息继续使用原版 sckocp。

Redis 协议、rsync 模块名、11 列 `.mon` 和版本化 JSON schema 保持兼容。
这些历史协议标识不代表仍在调用旧系统程序，不能用全局文字替换来修改。
sckocp 激活规则、Primary 时序输出限制、采样质量标注和进程身份检查保持不变。

0.3.0 使用独立目录。已有配置、未完成任务和修改过的安装文件不能被覆盖。
升级前需完成或明确处置待收尾批次，保存原包、连接配置及卸载/分离记录。
原始监控文件及已发布完成记录不应改写来配合名称迁移。

本文件记录实现目标；验收结论以对应提交的私有 Linux Actions 记录为准。
Windows 工作站不执行项目构建或测试，生产节点需另行按现场验收命令验证。
