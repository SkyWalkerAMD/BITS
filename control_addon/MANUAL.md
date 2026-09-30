# 原版 215 控制端查看插件 0.2.0

这是原版控制端的独立扩展 `mon-sensors-control`。安装在 `/opt/mon-sensors-control/0.2.0`，命令在 `/usr/local/bin/mon-sensors-control`，不改 `/home/ocuser/ocrun`、shell profile、ws/occt、数据库、NFS 挂载或后台服务。安装不启动任务，不需要安装 sckocp，不连接节点硬件，也不修改激活规则。

0.2.0增加v2回执支持：原三份数据之外核验详细报告 `.report.html` 与 `.report.json`，同时兼容v1历史记录。`show`/`verify`显示HTML路径；下载已核验HTML后用浏览器查看或打印。报告生成在节点完成，215不需要另装报表运行环境。

已安装0.1.0时，`--check`检查已发布版本哈希，`--apply`保留旧版目录并原子切换命令。未知或修改过的文件不覆盖。`--rollback`切回已验证的0.1.0命令，报告数据不动；旧版不认识v2回执，会明确拒绝。`--remove`移除两个已核验版本目录，保留原OCRUN和结果。

## 新系统和原系统的区别

- **新管理服务器**：使用 OCRUN 0.2.1 Release 的 `ocrun-center` RPM/DEB，显式配置地址、网段和节点凭据。新节点使用 `ocrun-node` 包，内含 MLC 3.13 等工具。完整过程见仓库 `DISTRIBUTION.md`。安装包成功不等于无需配置，也不等于硬件验收完成。
- **现有 215**：只安装本插件。任务继续由原 ws/occt 下发，原 217、211、221 分工保持。不要把完整管理端包安装到 215，也不要在这条路线运行新中心 setup。
- **原系统的被测节点**：继续用本地 sckocp-api、mon-sensors-plugin、报表与收尾扩展。已在 K6C-165 验证的组合继续有效；新工具需在选定节点单独安装并绑定工具套件，不能因为控制端安装本插件就视为节点已升级。

数据流：节点已激活的 sckocp → 本地 API/采集插件 → MON、JSONL、XLSX、收尾回执 → 原 211 上传入口 → 215 的 `/data/cds/result` → 本查看插件。已验证 211 与 215 读到的文件一致；不推定 211 和 221 的实际存储配置。

## 安装与现场最小验收

将 `mon-sensors-control-0.2.0.run` 和可信渠道取得的校验值传到 **215 的 root 账户**。先用发布清单核验 SHA-256，再执行：

```bash
bash /root/mon-sensors-control-0.2.0.run --check
bash /root/mon-sensors-control-0.2.0.run --apply
/usr/local/bin/mon-sensors-control --version
```

仅安装需要 root；以下查看使用 **215 的 ocuser 账户**，原 0600 的结果文件可以按原属主读取，无需改权限。系统 Python 3.6+ 标准库足够；EL8 优先 `/usr/libexec/platform-python`，不使用 `(base)` 的 Conda，不安装依赖、不替换系统 Python。

```bash
mon-sensors-control check
mon-sensors-control list --machine K6C-165_260168795800086
```

`check` 只读取你提供的 215 原版副本的五份关键文件哈希。已知版本为 MAIN_VERSION=0.9.19 / DEV_VERSION=0.9.20a。匹配输出 `original_files_match=true`；不匹配输出实际与预期哈希并返回 2，保留现场。版本文本里的旧合并标记不是待执行指令，不会被加载。这个检查不运行原脚本。原版修改不一定影响只读结果格式，但不标作已验证版本。

用之前已经验收的 RELIABILITY-020 文件验证（**ocuser**，只读）：

```bash
mon-sensors-control verify --receipt K6C-165_260168795800086/K6C-165_260168795800086_RELIABILITY-020_20260927-11.finish.json --receipt-sha256 3defd1806cc70a95ac9aa3910f67c288e10690585d223ae5d2abe9130598ccae
```

预期：`本机文件核验=通过`，`回执信任=matches_supplied_sha256`。它分别校验 MON、JSONL、XLSX 的大小和 SHA-256；这一回执的可信预期值来自此前节点及 215 现场输出。其他批次不要套用这个哈希。

查看最后一条已上传的监控样本：

```bash
mon-sensors-control sample --mon K6C-165_260168795800086/K6C-165_260168795800086_RELIABILITY-020_20260927-11.mon
```

每条命令可追加 `--json`，输出 `schema=mon-sensors-control-v1`，供其他本地程序读取。命令和退出码是本插件的本地接口；没有网络 API 或监听端口。

## 命令含义

| 命令 | 功能 | 说明 |
| --- | --- | --- |
| `check [--app 路径]` | 原版关键文件、结果根目录与解释器检查 | 只比对哈希，不加载原 oc.env |
| `list --machine 主机名_序列号 [--limit 20]` | 指定主机的批次列表 | 文件名倒序，不能当作严格时间排序；默认只读回执，不计算大文件哈希 |
| `show --receipt 主机目录/批次.finish.json` | 执行结果、采样质量、报表、交付声明、步骤和清单 | 详细步骤及完整文件列表用 `--json` |
| `verify --receipt ... [--receipt-sha256 哈希]` | 流式核对v1三份/v2五份数据与回执中的大小、哈希 | 非零退出码表示没有通过；不生成报表、不重新上传 |
| `sample --mon 主机目录/批次.mon` | 最后完整 CSV 样本及匹配的 JSON 详情 | `--json` 保留原接口的插槽/核心详情；不把上传样本当作实时数据 |

各命令支持 `--results-root /另一个明确的根目录`，默认 `/data/cds/result`。参数中的文件路径必须相对于这个根目录。按主机目录检索，避免递归扫描整个共享盘；单目录最多检查 2 万项、最多显示 100 个批次，超限用指定回执命令。单文件验证限 16 GiB，流式读取；JSON 回执限 1 MiB；样本只读 CSV 末尾 64 KiB 与 JSONL 末尾 2 MiB。超限报错，不删除、不截断原数据。

## 可信程度与错误处理

- `show/list` 是“回执报告了什么”，不会仅因回执存在就宣称 215 已核验文件。`verify` 才校验 215 实际读到的三份数据。
- 回执不含签名。不提供 `--receipt-sha256` 时标记 `unsigned_unpinned`：三份文件可与回执一致，但这不是来源认证。提供来自可信节点记录的哈希才增加回执一致性依据。
- 三份数据核验成功不等于 CPU 合格，也不掩盖执行提前退出。旧 0.1.0 回执缺少独立执行/质量字段时标记 `legacy_launch_status_only`、`legacy_quality_not_recorded`。
- v1 的传感器有效性、读数年龄未知；VRM、整机功耗、风扇缺失仍为 null，不填 0。附件只有时间及任务与 CSV 匹配才显示；不匹配明确记录，不拼接成同一个样本。
- 本插件没有节点实时连接。没有回执可能是尚未收尾、上传未完成或已经失败，不能推断“正在压测”。需要在对应节点查看 `mon-sensors-finish status --human`；恢复仍在节点执行，不由控制端偷偷重跑压测。
- 结果目录允许原 ocuser 属主；文件一律作为数据读取。拒绝路径穿越、符号链接、FIFO/设备和未知回执格式。程序安装目录另行要求 root 属主及不可组写/他人写，不因为 NFS 数据属主而放宽程序检查。
- 校验期间检测到文件替换或修改就失败，等待上传完成后重试。校验是读取时点的观察，无法保证之后无人修改；NFS 的连通性和缓存仍由现有服务器管理。共享盘卡住时可给命令套 `timeout 30s`；内核不可中断 I/O 不能保证在时限内退出。

## 撤回及重复安装

保留原安装器和校验值。root 执行：

```bash
bash /root/mon-sensors-control-0.2.0.run --remove
```

只移除本插件的已核验命令及固定版本目录，不删监控数据或原 OCRUN，也不需要修改原 ws/occt。重复安装相同包会核对已有内容，确认相同才复用。存在未知文件、被修改的程序、非本插件占用的命令或未知版本时拒绝覆盖；保留输出及现场后处理。若安装在发布命令前中断且版本目录完整，同一安装器可继续；若出现不完整 `.install-*` 目录会保留并拒绝继续，先人工核验，不能强制覆盖。

## 验证边界

新完整系统 0.2.1 已在 12 个发行版环境通过云端包安装、认证任务链路及结果交付验收（运行 36364103021），不等于这些系统都完成实际传感器/硬件验收。

本插件的云端工作流 `Original controller read-only plugin` 单独验证 EL8/Python3.6 与较新 Python 环境的安装、普通用户读取、旧/新回执、错误与未知字段、文件损坏、缺失、符号链接、重复安装和撤回；测试使用合成数据，没有连接生产 Redis、NFS 或节点。运行、源码提交、实际 OS/Python 和结果随交付 `RELEASE.json`/`validation` 提供。215 AlmaLinux8.7 的真实挂载、权限和五份原文件是否仍一致，以用户现场最小验收为准。
