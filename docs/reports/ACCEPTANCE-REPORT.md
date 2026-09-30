# 机器压测报告单：操作与验收

用于内部验收的离线 HTML 报告。完整系统 0.2.2 与原版节点的 `mon-sensors-finish 0.2.5` 共用生成器；215 使用独立查看插件 `mon-sensors-control 0.2.0`。不改变原 ws/occt、Redis 任务协议、sckocp 授权和自动关机策略，不增加监听服务。

## 报告内容

- **机器配置**：批次开始时读取的系统型号、DMI 序列号、主板和 BIOS、CPU 型号/插槽/核心数、任务可用 CPU 数、OS 可见内存、块设备型号与容量、操作系统和内核。来自本机 OS、procfs 和 sysfs，不执行资产采集命令；缺少的信息标记未知。内存条槽位、型号、频率不在此快照范围内。
- **任务过程**：逐步记录任务名、工具版本（已知时）、程序路径/哈希、配置秒数、实际秒数、起止时间、退出原因/退出码、进程清理确认。重复任务逐项保留。解释器型工具的程序哈希标识解释器，工具套件的完整清单另由安装包验证。
- **监控统计**：有值、缺失和零值数量；负载、频率、CPU/VRM 温度、CPU/整机功耗的最小、均值、最大；各任务标签、插槽与核心统计；温度/功耗/频率趋势。
- **异常与证据**：不可用采样、超过6秒的采样间隔、运行时长倒退、提前退出或清理未确认、配置缺项，以及原结果文件的大小和 SHA-256。

报告不会自动判断“硬件合格”。任务按时结束、数据有数值、报表生成、文件交付是不同结论。当前没有客户指定的温度/功耗阈值，也未解析每个压测工具的内部自检输出，不能据此宣称零计算错误。原版 sckocp v1 不提供传感器有效性与读数年龄；缺失值不补0，零值也不证明采集成功。

均值为采样算术均值，不是时间加权或耗电量。趋势最多240个连续样本均值点，每个样本参与统计；峰值仍看汇总表。横轴为样本顺序而非等距实际时间。重复项目的监控统计按同名采集标签合并，执行过程仍逐步列出。任务起止时间为 UTC，MON 时间为节点本地时间；采样间隔依据运行时长计算。

## 自动生成与文件核验

新批次结束后执行：停止采集 → 封存/校验 → Excel → HTML/报告JSON → 上传并下载核对哈希 → 发布完成回执。

每个正常批次共有六份文件：

| 文件 | 用途 |
| --- | --- |
| `批次.mon` | 原十一列监控记录 |
| `批次.mon.sckocp.jsonl` | 原始接口与采集上下文 |
| `批次.xlsx` | 原 Excel 明细及汇总 |
| `批次.report.html` | 可直接浏览/打印的报告单，无外部资源或脚本 |
| `批次.report.json` | 结构化报告数据，schema 为 `ocrun-acceptance-report-v1` |
| `批次.finish.json` | v2 完成回执，列出上述五份文件的大小和哈希 |

HTML 在上传前生成，所以它明确写明交付结果需核验独立回执，不会提前自称上传成功。回执自身哈希保留在节点状态中，可用它在控制端固定信任来源。SHA-256 不是数字签名。

历史批次保持原 v1 回执与三份数据，不重新生成文件、不用当前配置冒充历史快照。旧版查看插件0.1.0不认识v2回执，会明确拒绝；升级查看插件后同时支持v1和v2。原来的 `.mon` 与 Excel 消费方式继续可用。

## 完整系统

按 `README.md` / `DISTRIBUTION.md` 安装或升级 0.2.2 原生 RPM/DEB。新批次自动生成报告，操作入口不变：

```bash
ocrun-node preflight
ocrun-node start
ocrun-node status --human
```

新管理服务器上，使用有读取权限的账户（通常 ocuser）：

```bash
ocrun-center results list --machine TEST-NODE_SERIAL
ocrun-center results verify --receipt TEST-NODE_SERIAL/BATCH.finish.json
```

把示例中的机器目录和批次文件替换成 `list` 的实际值。结果物理目录是 `/srv/ocrun/logs`；原入口 `/data/cds/result` 为兼容链接。新命令默认使用物理目录，保留严格的无符号链接读取策略。`verify` 同时给出 Excel 和 HTML 路径，支持 `--receipt-sha256` 与 `--json`。

通过已有 SFTP 将核验后的 `.report.html` 下载到工作电脑，双击浏览器打开；打印或“另存为 PDF”，选择 A4 横向。HTML 自包含，可独立查看，不要求工作电脑安装 Python，不会发网络请求。需要审计时六份文件一起归档。

## 原版节点与215插件

**仅指定测试节点 K6C-165，root**：等本批任务结束，先查看 `status --human` 并停止空闲调度。安装预检会拒绝仍在运行的任务或未处理的收尾记录，不应使用按名称批量杀进程的方式绕过。

使用发行总包 `standalone/mon-sensors-finish-0.2.5.run`，先在总包目录核验 `SHA256SUMS`。原版 sckocp、API、采集插件和现有报表组件保持已装状态；本次只升级收尾生成报告功能。

```bash
bash ./mon-sensors-finish-0.2.5.run --app /root/ocrun --check
bash ./mon-sensors-finish-0.2.5.run --app /root/ocrun
/root/ocrun/mon-sensors-finish check --scheduler
```

支持已知收尾0.1.0及0.2.0–0.2.4受管文件的升级。手工改动和未知版本保留并拒绝覆盖。旧节点工具版本无法确认时报告标记未知，不执行程序来猜版本。

**215，root安装、ocuser查看**：使用独立 `mon-sensors-control-0.2.0.run`：

```bash
bash ./mon-sensors-control-0.2.0.run --check
bash ./mon-sensors-control-0.2.0.run --apply
```

回到原来的 ocuser 环境：

```bash
mon-sensors-control check
mon-sensors-control list --machine K6C-165_260168795800086
mon-sensors-control verify --receipt K6C-165_260168795800086/BATCH.finish.json
```

插件只读取215已有的 `/data/cds/result`，不会升级 `/home/ocuser/ocrun`、更改原命令、接管任务或访问数据库。不需要在215安装节点包。0.1.0查看插件原文件按发布哈希验证，升级保留旧版本用于回滚。

## 失败、恢复和回滚

状态 `acceptance_report` 带错误表示报告尚未成功；不会发布成功回执，也不会忽略待办继续自动关机。检查错误及磁盘后，在对应节点执行已有入口：

```bash
# 完整系统
ocrun-node retry --case CASE_ID
# 原版节点插件
/root/ocrun/mon-sensors-finish retry --case CASE_ID
```

恢复不重新跑压测，已确认的原数据和Excel哈希不变。JSON写出后HTML写出前中断可重试；已存在但内容不同的报告会保留并报错。不要删改封存文件来绕过核验。未封存中断使用已有显式 `recover --case CASE_ID --interrupted`；空日志/损坏且无法恢复的批次不能制造成功报告，可按原规则保留证据后 `close-incomplete`。

统计逐行处理，不把长日志全部加载。原4百万行/16GiB输入上限保留；汇总最多128种任务标签、4096个CPU、128个插槽、32个来源版本。每份报告限16MiB，超限明确失败并保留数据，后续任务应分批。不静默截断原数据或删除历史文件。

旧节点插件回滚：无运行任务且所有待办已处理时，使用本次安装器：

```bash
bash ./mon-sensors-finish-0.2.5.run --app /root/ocrun --rollback --check
bash ./mon-sensors-finish-0.2.5.run --app /root/ocrun --rollback
```

215查看插件若从0.1.0升级，可 `bash ./mon-sensors-control-0.2.0.run --rollback` 切回已核验的旧程序；`--remove`移除本查看插件的两个已核验版本，保留原OCRUN和数据。原生包按主手册显式detach/卸载与旧包回装，不强行覆盖在用配置。已有报告和回执永远保留。

## 最小现场验收

1. K6C-165 root 升级收尾插件；215 root 安装查看插件后切回 ocuser。不要触碰 K6C-183 的在跑任务。
2. 仍用原215任务菜单，创建新编号 `REPORT-CHECK`，stress60秒与stress-ng60秒；节点预检后明确start。不要重用已封存的批次标识。
3. 节点 `status --human` 应为complete；JSON状态中执行达到时长、清理确认，回执版本v2，数据清单5项加回执1项。监控质量仍标注未知有效性。
4. 215 ocuser 用 `verify` 核对六份材料，下载HTML，检查机器配置、两个步骤、统计、缺失列和打印版式。确认原ws/occt与历史v1批次仍可读取。

云端证据区分真实包/服务/进程和合成传感器数据。真实硬件信息完整性、215真实NFS和人工验收结论仍由上述现场确认；发布包中的 `RELEASE.json` 记录具体源码与云端运行，不以演示报告冒充现场结果。
# BITS 0.2.4 新增监控与配置内容

HTML 与报告 JSON 的机器配置部分展示原生 Platform 全部已提供字段、CPU family/model/stepping/微码、Turbo/温控/功耗配置、PSU 和 DIMM 配置快照。内存时序**仅收录 Primary 组**，包括原生同组 tCWL/tRC（如提供）；其他组禁止经 API 导出，不受本机 rmal 解锁状态影响。

监控统计部分按来源与插槽展示 Pkg、DRAM 功耗、内存温度、VCCIN、VID、TjMax；另列整机 PSU 输入、各 PSU 和 DIMM 温度。整机读数不按插槽累加。核心表按数字 0、1、2… 排列，增加 VID。统计包含样本计数，补充读数默认约 10 秒一次并保留其独立时间；部分 PSU 读数与完整总功耗分开统计。

保留第一份及最后一份 info 配置，并记录期间配置变化次数；中间采集在 JSONL 附件中。原 `.mon` 与 Excel 仍为 11 列兼容明细，新增项目在 HTML / `.report.json` / `.mon.sckocp.jsonl`。旧批次不会使用当前机器的新信息补写；未提供项、未知有效性和年龄继续如实显示。
