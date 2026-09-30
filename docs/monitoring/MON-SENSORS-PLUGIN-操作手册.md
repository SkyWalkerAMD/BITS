# mon-sensors-plugin 0.12.12 操作手册

适用安装包：`mon-sensors-plugin-0.12.12.run`，内含 `sckocp-api 0.3.2` 核心。适配已安装原版 0730 OCRUN 的被测设备，默认读取原版 sckocp 1.1.0 / 1.2.0 的 `mon --json`。

## 1. 安装位置和前提

本包安装在每台需要采集的**被测设备**上。现有管理服务器的操作系统、0730 OCRUN 和网络上传方式保持原样；单纯把安装包放到管理服务器，不会自动安装到节点。

- 设备已有完整 0730 OCRUN，并能执行原有任务；插件不是完整设备端安装包。
- 设备已有可正常使用、已激活且完成所需平台登记的 sckocp；插件不代办激活。
- 设备具有 `bash`、Linux `/proc`、常用的 `tar` / `sha256sum` 等系统工具，以及标准路径中的 `python3`，Python 版本为 3.6 或以上。安装器不联网安装依赖。
- 安装、硬件采集及停止 root 启动的监控按 root 使用。帮助可以普通用户查看，但需有目录访问权限；插件不会自行提权。
- 插件已经包含 API 核心，**不需要再安装独立 sckocp-api 包**。第三方软件单独调用接口时才按另一份手册安装。

以下示例假定设备 OCRUN 目录为 `/root/ocrun`，安装包在当前目录。其他路径请统一替换；原 0730 脚本自身仍依赖 `~/ocrun/oc.env`，显式选择安装目录不会重写原版环境配置。

## 2. 首次安装

先通过原任务管理流程停止压测任务，再停止监控：

```bash
/root/ocrun/oct killm
```

`killm` 只负责监控，不停止压测。首次安装前，原版 `killm` 按进程名称停止监控，可能影响同机其他 OCRUN 实例；安装本插件后才改为限定应用路径。安装器不会代替你停止任务；检测到该应用监控仍在运行时会拒绝安装。

先做预检查，再正式安装：

```bash
bash mon-sensors-plugin-0.12.12.run --app /root/ocrun --backend sckocp --check
bash mon-sensors-plugin-0.12.12.run --app /root/ocrun --backend sckocp
```

`--check` 检查路径、权限、脚本兼容性、已管理文件和监控状态，输出安装计划，不修改目标应用；它不执行硬件采集或检查激活是否有效。`.run` 的临时解包仍会发生，退出时清理。

也可自动查找安装目录：

```bash
bash mon-sensors-plugin-0.12.12.run --check
bash mon-sensors-plugin-0.12.12.run
```

自动查找用户家目录、`/root/ocrun`、`/opt/ocrun`、`/usr/local/ocrun` 及已知命令路径中的兼容应用，只接受唯一候选；找不到或发现多个时使用 `--app`。首次自动发现安装默认启用 sckocp，升级保留既有来源。

显式指定 `--app` 却省略 `--backend` 时保留已有选择；新设备尚无配置时保持 `legacy`。要确保本次启用 sckocp，请像第一个示例一样明确指定来源。

| 安装选项 | 含义 |
| --- | --- |
| `--app /绝对路径/ocrun` | 指定已有 0730 设备端目录；省略时自动查找 |
| `--backend sckocp` | 持久使用 sckocp 采集 |
| `--backend legacy` | 持久使用原主板采集 |
| `--backend auto` | 启动旧监控入口时，发现 sckocp 则选择它，否则使用原来源 |
| `--check` | 只检查并打印计划 |
| `--source /绝对路径/安装源` | 高级选项；默认使用自动确定的解包目录，正常安装无需指定 |
| `--help` | 查看安装器帮助 |

成功会输出 JSON，`status` 为 `installed` 或 `already_installed`，`backend` 显示最终来源；预检查为 `checked`。发生实际变更前，备份保存在 `/root/ocrun/.mon-sensors-backups/before-*`。安装失败会尝试回退；文件系统故障也可能阻止回退，备份目录会保留供核对。

安装后新增 `/root/ocrun/mon-sensors-plugin` 和同级 `mon-sensors-plugin.d/`，并给旧入口添加适配。`.run` 不会把安装器长期保存在 `/opt`；请保留原 `.run` 供升级、重装和来源切换。

## 3. 先采一次，再连续查看

查看帮助和单次数据：

```bash
/root/ocrun/mon-sensors-plugin --help
/root/ocrun/mon-sensors-plugin --once
echo "$?"
```

正常时终端显示说明、11 列表头和一行数据；这是面向 OCRUN 的表格，**不是 JSON API 输出**。状态提示写入标准错误。`--once` 无论成功失败都只调用一次，不进行重试。插件没有 `--version` 参数。

每 5 秒进行一轮采集，在前台持续显示：

```bash
/root/ocrun/mon-sensors-plugin 5
```

按 `Ctrl+C` 正常停止。采集串行执行；一轮耗时超过设定周期时，不重叠采集、不补跑已经错过的轮次。

自定义采样窗口、超时和任务标签：

```bash
/root/ocrun/mon-sensors-plugin 5 view --sample-window 1 --timeout 30 --task burnin01
```

这里 `5` 是轮次周期，`1` 是原生监控的采样窗口，`30` 是单次调用最长等待时间，三者用途不同。任务标签只影响日志显示，不会启动或调度压测。

## 4. 命令参数完整表

语法：`mon-sensors-plugin [interval] [logfile] [选项]`。第二个位置参数前必须先给周期，例如 `5 /var/log/mon-sensors-plugin/demo.mon`。

| 参数 | 默认值 | 范围和用途 |
| --- | --- | --- |
| `interval` | `5` | 每轮采集周期，0.05–3600 秒；不是采样窗口 |
| `logfile` | `view` | `view` 表示终端显示；其他值为日志路径，父目录须存在 |
| `--once` | 关闭 | 只进行一次采集，不重试 |
| `--binary /绝对路径/sckocp` | 见下文 | 指定可信的 sckocp 可执行文件 |
| `--format v1` / `v2` | `v1` | 原生 JSON 格式；可由 `SCKOCP_FORMAT` 设置默认值 |
| `--timeout 秒` | `20` | 0.1–120，必须大于 `--sample-window` |
| `--sample-window 秒` | `1` | 0.05–60，传给原生监控的采样窗口 |
| `--max-age 秒` | `30` | 0.05–600；v2 检查原生指标年龄加本地耗时，v1 只能检查收到响应后的本地耗时 |
| `--retries 次数` | `2` | 整数 0–5，仅重试连续的 `timeout` / `collection_failed` |
| `--retry-delay 秒` | `1` | 0.1–30，暂时故障后的重试等待时间 |
| `--task 标签` | 自动检测 | 非空、最长 128 字符；不允许逗号、双引号、竖线、控制字符或首尾空白 |
| `--stop-app /绝对路径/ocrun` | 不执行 | 停止指定应用目录下识别到的监控入口，不采集 |
| `--help` / `-h` | — | 查看参数帮助后退出 |

所有秒数必须是有限数值。v1 没有传感器真实读数年龄，不能通过调低 `--max-age` 获得新鲜度保证。`v2` 仅供已经具备原生 v2 扩展的设备使用；原版 1.1.0 / 1.2.0 继续使用 `v1`，本插件不会给本体增加 v2。

重试数是首次失败后允许的额外尝试：默认连续失败最多执行 3 次，每次失败仍记录空指标；任一次成功后重置计数。`--retries 0` 表示首次失败即退出。授权、平台登记、程序信任、权限、数据格式和配置错误都不重试，也不换用其他采集来源。

## 5. 非标准 sckocp 路径

程序路径按 `--binary`、非空 `SCKOCP_BINARY`、PATH 中的 `sckocp`、`/usr/bin/sckocp` 依次确定。

```bash
/root/ocrun/mon-sensors-plugin --once --binary /opt/sckocp/sckocp
SCKOCP_BINARY=/opt/sckocp/sckocp /root/ocrun/mon-sensors-plugin --once
```

通过旧 `oct` / `ocb` 长期使用自定义路径时，在设备原有 `oc.env` 中配置 `export SCKOCP_BINARY=/opt/sckocp/sckocp`，然后重启相应设备进程；已运行进程不会自动获得新环境。直接执行插件不会读取 `oc.env`，需传 `--binary` 或在其启动环境中导出变量。

root 采集时，可执行文件及路径必须由 root 维护，不可被普通用户写入；接口也会拒绝特殊文件、危险权限和不可信路径。不要用 `chmod 777` 解决权限错误，也不要把任意程序路径的 root 执行能力开放给普通调用者。

## 6. 写入日志

以下独立示例创建一个私有目录，在前台每 5 秒写一轮，按 `Ctrl+C` 停止：

```bash
install -d -m 0700 /var/log/mon-sensors-plugin
/root/ocrun/mon-sensors-plugin 5 /var/log/mon-sensors-plugin/burnin01.mon --task burnin01
```

生成两个文件：

- `burnin01.mon`：两行说明和表头，其后为原 0730 兼容的 11 列数据。
- `burnin01.mon.sckocp.jsonl`：每行一份完整响应及系统上下文，采集状态位于 `provider.status`，指标位于 `provider.data`。

文件均为 `0600`。只有同一用户拥有、普通、单链接、不可被组或其他用户写入的合规日志才能追加；拒绝软链接、硬链接、格式不符和其他进程正在写入的日志。v1、v2 以及原主板日志不能混写，切换来源或格式时请使用新文件名。

只向日志写一条：

```bash
/root/ocrun/mon-sensors-plugin 5 /var/log/mon-sensors-plugin/check01.mon --once
tail -n 1 /var/log/mon-sensors-plugin/check01.mon.sckocp.jsonl
```

此示例目录不属于原 OCRUN 的 `LOGPATH`，`oct push` **不会自动上传这里的文件**。正式接入原管理流程请使用下一节的 `oct mon`，或明确把自定义日志放到设备原有 `LOGPATH` 中。

11 列依次为类型、时间、当前系统任务、运行时长、1 分钟负载、主频、CPU 温度、VRM 温度、CPU 功耗、整机功耗、风扇转速。v1 主频取返回核心的 MHz 均值，温度取返回插槽温度最大值，CPU 功耗取返回插槽功耗总和；缺失参与项时对应汇总为空。

“运行时长”是操作系统启动至今的秒数，不是当前压测任务耗时；“负载”是 1 分钟系统负载，不是 CPU 使用率百分比。

缺失的 AMD 温度、VRM、整机功耗和风扇数据保持空白，不补零。CPU 功耗不等于整机功耗。v1 返回的零也可能代表原生读取失败，日志中的 `reported-validity-and-age-unknown` 表示有效性及年龄未知，不能用这些读数证明硬件合格或充当温度保护。

## 7. 沿用 0730 任务、监控、报表和上传

设备安装并持久选择 `sckocp` 后，原管理服务器继续分发原来的任务，设备旧 `ocb` / `oct` 流程通过 `mon-sensors` 转调插件。直接执行 `mon-sensors-plugin` 总是采集 sckocp，不受 `legacy` 来源选择影响。

手动启动一份监控时，先确认没有同一任务的监控正在运行：

```bash
/root/ocrun/oct mon burnin01 20260926_120000
```

该命令只在后台启动监控，周期固定为 2 秒，不会启动压测。第二、三参数分别是任务 ID 和用于日志文件名的时间标签；省略时为 `RedSun` 和 `oc.env` 中的 `APP_DATE`。日志在原 `LOGPATH` 下，名称为 `主机名_主板序列号_任务ID_时间标签.mon`，同时生成 JSON 附件。

结束时按原任务流程停止压测，再执行：

```bash
/root/ocrun/oct killm
/root/ocrun/oct analyse
/root/ocrun/oct push
```

- `killm` 停止该 OCRUN 应用下识别到的监控入口并清理其采集子进程，不停止压测。也可直接执行 `/root/ocrun/mon-sensors-plugin --stop-app /root/ocrun`；它不是“停止整台设备所有测试”的命令。
- `analyse` 分析 `LOGPATH` 中按修改时间最新的 `.mon`，默认工作簿为 `${HOSTNAME}-Analyse.xlsx`，同名文件可能被覆盖。需要保留历史报表时先另存旧工作簿。Excel 依赖原分析环境中的 pandas、openpyxl 和 XlsxWriter；采集本身不需要它们。
- `push` 沿用原 rsync 配置，把 `LOGPATH` 中的文件和目录上传到 `${LOGSVR}::logs/${HOSTNAME}_${MB_SN}`，包括 `.mon`、JSON 附件及设备生成的 Excel。以 root 采集的私有日志应由 root 执行上传。

原 `oct` 的 `analyse` / `push` 分支不向内部函数转发额外参数；不要用 `oct analyse 指定文件 新报表.xlsx` 或 `oct push 自定义目录` 来选文件或目的地。`oct mon` 的任务标签仅用于命名，不保证 `.mon` 内的当前任务列与其相同；该列由插件检测进程，手工插件命令可用 `--task` 覆盖。

旧服务器会接收 JSON 附件，但不会自动在界面展示新增逐核心数据。设备端分析器已兼容全空传感器列，旧服务器自身分析器的全空列缺陷未修改，因此 Excel 在设备生成后再上传。采集失败也不会让旧 `ocb` 自动停止压测，仍需原有任务管理及硬件保护措施。

## 8. 采集来源切换

来源设置只控制旧 `mon-sensors` 入口。持久选择写在 `/root/ocrun/.mon-sensors-backend`，环境变量 `MON_SENSORS_BACKEND` 优先级更高；显式设置为空值等同 `legacy`。

停止该设备监控后，通过保留的安装包切换：

```bash
bash mon-sensors-plugin-0.12.12.run --app /root/ocrun --backend legacy
bash mon-sensors-plugin-0.12.12.run --app /root/ocrun --backend sckocp
```

以上两条分别是切回原采集和重新启用 sckocp，按需要选择一条。`auto` 仅在明确指定时启用；一旦选中 sckocp，其授权或采集失败不会自动退回主板采集。

只覆盖一次调用：

```bash
MON_SENSORS_BACKEND=sckocp /root/ocrun/mon-sensors --once
```

插件选项仅在转调插件时适用；不要给 `legacy` 模式的原脚本套用插件的 `--once` 等参数。改变配置不影响已经运行的监控，需重新启动。

## 9. 状态、退出码与排查

插件命令退出码：`0` 为单次成功、正常中断或停止命令成功；`1` 为采集失败、日志失败或不能安全停止；`2` 为参数错误。安装器另有自己的返回结果：正常安装、已安装及预检查返回 `0`，检查或安装失败返回 `1`，参数解析错误返回 `2`。后台启动的 `oct mon` 返回不代表第一轮采集已成功，应查看日志和状态。

| `provider.status` / 现象 | 处理方法 |
| --- | --- |
| `ok` | 本轮已得到合规响应；v1 指标质量限制仍适用 |
| `license_denied` / `license_unavailable` | 检查原版激活及原授权服务可用性；接口不绕过授权，也不提供独立离线授权 |
| `unkeyed_build` / `platform_required` | 使用正确的原版构建，按原流程完成平台登记 |
| `unavailable` | 核对 sckocp 路径，用 `--binary` 指定实际绝对路径 |
| `unsafe_executable` / `permission_denied` | 核对属主、路径可写权限、执行权限和调用身份；不要降低安全检查 |
| `timeout` / `collection_failed` | 检查原监控、驱动、设备权限或授权续期链路；临时故障有限重试，可按实际情况调整超时 |
| `invalid_data` / `unsupported_schema` / `output_limit` | 原版应使用 v1；核对程序版本和输出格式，不靠反复重试掩盖格式问题 |
| `invalid_configuration` / `unsupported_platform` | 核对参数范围和 Linux 环境 |
| `Cannot safely open or write monitoring logs` | 核对父目录、文件属主、格式、权限、磁盘空间以及是否有另一采集进程；优先使用新的日志文件 |
| 安装提示多个候选目录 | 使用 `--app` 明确选择设备目录 |
| 安装提示本地文件被修改 | 保留现有修改和备份，先核对差异；不要删除安装记录强制覆盖 |

失败响应不携带上次成功的数据，日志中的空白应视为不可用。进一步诊断可在设备上按原版文档运行原 `sckocp mon`，确认本体是否正常；此步骤仍会触发原本的硬件及授权操作。

## 10. 升级、原工具同步和恢复

升级前停止压测和监控，保留安装包及已有备份，再对新版本执行相同的 `--check`、安装命令。升级保留已有来源；如果明确传入 `--backend`，则按该参数切换。安装器支持重复执行和恢复缺少的已管理依赖，不覆盖被自行修改的已管理文件。

原设备以后执行 `ocsync` 可能覆盖适配脚本。应在原同步完成、设备监控停止后重新运行本插件安装包；不要通过升级管理服务器 OCRUN 来恢复该适配。

`--backend legacy` 是停用 sckocp 接入并保留插件，**不是卸载**。当前没有通用的一键卸载命令；`.mon-sensors-backups/before-*` 是每次安装前的快照，不保证任何一个目录就是初始原版。需要完整恢复时，先核对对应备份时间和文件，再按整套匹配版本处理，避免混用不同快照。

相关说明：[独立 API 操作手册](SCKOCP-API-操作手册.md)、[插件设计和兼容范围](MON-SENSORS.md)、[分发包说明](../archive/monitoring-0.3.1/PACKAGES.md)、[云端验证记录](../archive/validation-20260926/CLOUD-RESULTS.md)。真实硬件和生产授权仍需设备验收；接口加固不能保证阻止设备 root 修改 sckocp 本体。
