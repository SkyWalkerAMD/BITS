# mon-sensors-plugin：0730 设备端可选采集插件

`mon-sensors-plugin-0.12.12.tar.gz` 安装在**被测设备**上，为已有 0730 增加独立的 `mon-sensors-plugin` 命令。插件通过独立 `sckocp-api 0.3.2` 读取原版 sckocp 1.1.0/1.2.0 的 `mon --json`，沿用原激活检查，无需升级或给 sckocp 打补丁。

新增采集和安装代码位于 `mon_sensors_plugin/`，安装后放在设备的 `mon-sensors-plugin.d/` 中。插件包不包含 `ocrun/` 业务模块。原 `mon-sensors` 保留主板采集功能，只增加一个可选转调入口；原有 `oct mon/killm/analyse/push` 继续使用。

**管理服务器的操作系统和 0730 OCRUN 均不需要升级。** 保留旧任务调度、日志目录、11 列 `.mon` 和 `oct push` 的 rsync 上传方式。不要为此运行早期 Agent 的服务器安装器；它使用不同任务协议。当前完整系统和统一旧系统套件的入口见[安装导航](../README.md)。

适配包自带独立接口所需的 Python 模块，无需重复安装另一个接口包。独立接口仍可单独分发给其他软件，详见 [公共接口说明](SCKOCP-API.md)。没有新网络服务。

本采集组件是已有 0730 设备端的增量包，不是新设备的完整 OCRUN 安装包。需要任务管理、报表及自动收尾时，使用[统一 ocrun-plugin 套件](../plugins/OCRUN-PLUGIN.md)。早期三包拆分见[历史分发说明](../archive/monitoring-0.3.1/PACKAGES.md)。

## 在被测设备上安装和启用

### 0.12.9 现场适配

此版本额外核对资源版 `0.9.24a` 的四个桥接目标，覆盖 K6C-183 的已知文件哈希。已有独立接口 `sckocp-api 0.3.2` 可继续使用；这次更新只需分发设备插件。

某些原版设备的 OCRUN 主目录属于 root，脚本和 `py` 目录却保留分发包的 UID 201:GID 200。安装器默认仍拒绝这些文件。仅在停止压测和监控后，使用以下显式选项进行接管：

```sh
bash mon-sensors-plugin-0.12.12.run --app /root/ocrun --backend sckocp --adopt-original --check
bash mon-sensors-plugin-0.12.12.run --app /root/ocrun --backend sckocp --adopt-original
```

`--adopt-original` 要求 root、可信的主目录、四个文件完整匹配同一个已知原版（0730 或 0.9.24a）以及预期的权限和链接状态。检查不修改目标。实际安装只接管 `py` 目录及原有四个桥接目标；不递归修改其他文件。不匹配时停止，不能靠放宽权限跳过校验。备份包含原始内容、模式、UID/GID 和接管记录；安装失败会尝试恢复这些内容。手工回退时也需恢复权限记录，不应只复制备份文件的 0600 模式。

同一 OCRUN 目录的后台文件采集现在共用应用锁，覆盖原采集后端和 sckocp 后端，重复启动返回 75。直接插件命令也遵守此锁。`view` 和 `--once` 仍允许临时查看，文件自身还有独占写锁。安装不会清理旧的重复进程；应在维护窗口确认压测结束后处理。

安装后的 `oct killm` 会先校验监督进程的身份并停止它管理的采集进程组，再处理该应用目录的旧采集入口。它不代表停止压测任务；原版 `oct killa` 的行为没有改动。

首次现场验证可先解压 `.tar.gz`，用 `/usr/libexec/platform-python -I -B /解压目录/mon-sensors-plugin 2 /私有临时目录/probe.mon --once --binary /实际路径/sckocp`，检查 11 列日志及 JSON 辅助日志。此方式不安装桥接，不写正在使用的压测日志。

插件日志采用私有权限。正式启用前还需验证 rsync 接收后控制端 `ocuser` 的实际读取权限；历史旧日志可读不代表新增插件日志也可读。不要用全局可写权限来解决这个问题。

### 常规安装

推荐使用新增的离线自动安装包。在被测设备先停止压测和监控，以 root 执行一条命令，无需手动解压：

```sh
bash mon-sensors-plugin-0.12.12.run
```

安装器查找常见位置的 0730 目录，只有唯一兼容目录时才自动选择。自动发现的新装设备默认启用 `sckocp`；升级保留已有来源。多个候选、运行中的监控、不安全路径或被自行修改的已管理文件会明确报错，不自动停止任务。非标准位置可以指定目录及来源：

```sh
bash mon-sensors-plugin-0.12.12.run --app /root/ocrun --backend sckocp
# 先查看检查结果和安装计划，不修改目标文件
bash mon-sensors-plugin-0.12.12.run --app /root/ocrun --backend sckocp --check
```

再次执行相同命令即可升级或检查是否已经安装。显式指定目录而省略 `--backend` 时保留原选择；没有配置的新装设备保持 `legacy`，兼容旧安装命令。设备更新原 0730 脚本后，可停监控再重新运行安装包。

要求 Python 3.6+，兼容只有 `/usr/libexec/platform-python` 的精简 EL8 节点。安装后入口固定使用经过路径校验的解释器，使用 `-I -B` 隔离导入，运行不依赖 PATH 中的 `python3`。采集仅用标准库；sckocp 需提前安装、激活并满足原有平台登记、驱动和权限要求。安装器不访问软件仓库，不安装 Python 或替换系统解释器；缺少依赖时给出提示，便于使用内网软件源。安装过程不执行硬件采集或激活操作。`.run` 会校验内嵌归档再解包到私有临时目录，结束后清理；校验用于发现文件损坏，不代替可信分发渠道或发布者签名。

原 `.tar.gz` 方式继续可用，适合手工检查包内容后安装：

```sh
mkdir -p /opt/mon-sensors-plugin-0.12.12
tar -xzf mon-sensors-plugin-0.12.12.tar.gz -C /opt/mon-sensors-plugin-0.12.12
bash /opt/mon-sensors-plugin-0.12.12/install-mon-sensors-plugin.sh /root/ocrun --backend sckocp
```

`/root/ocrun` 是被测设备已有的 0730 目录，按实际路径调整。`--backend sckocp` 将选择保存在设备的 `.mon-sensors-backend`，后续新启动的旧版任务和监控均可使用；无需修改管理服务器配置。安装不会启动监控或压测。

显式指定安装目录时，省略 `--backend` 会保留设备已有选择；首次安装且无配置时仍使用原采集方式。显式设置的 `MON_SENSORS_BACKEND` 环境变量优先于设备配置，空值等同 `legacy`。安装器不修改 `oc.env`、主板采集函数、服务器地址、凭据或 sckocp。

程序路径默认在设备 PATH 中查找，找不到时使用 `/usr/bin/sckocp`。非标准路径可通过 `SCKOCP_BINARY=/实际绝对路径/sckocp` 指定；要让旧守护程序持续使用该路径，可在**设备端**已有的 `oc.env` 中配置对应 `export`，并重新启动相关设备进程。不要给不可信调用者授予任意 `SCKOCP_BINARY` 的管理员执行权限。

## 使用与切换

```sh
# 直接调用独立插件查看一次 sckocp 数据
/root/ocrun/mon-sensors-plugin --once

# 原入口仍可使用；安装时已持久选择 sckocp 才会转调插件
/root/ocrun/mon-sensors --once

# 沿用旧任务入口开始监控，完成后停止
/root/ocrun/oct mon test01 20260922
/root/ocrun/oct killm

# 在被测设备上生成 Excel，再沿原通道上传日志及报表
/root/ocrun/oct analyse
/root/ocrun/oct push
```

Excel 导出仍需要旧分析器原有的 pandas、openpyxl、XlsxWriter 环境，采集本身不依赖这些库。请输出到新的工作簿；旧分析器可能覆盖同名工作簿。

直接执行 `mon-sensors-plugin` 就是调用 sckocp 插件，不读取旧入口的来源选择。`MON_SENSORS_BACKEND` 和 `.mon-sensors-backend` 控制的是原 `mon-sensors` 是否转调插件；选择 `legacy` 后，直接执行插件仍然读取 sckocp。

临时覆盖设备选择：

```sh
MON_SENSORS_BACKEND=legacy /root/ocrun/mon-sensors
MON_SENSORS_BACKEND=sckocp /root/ocrun/mon-sensors 2 /root/log/new-test.mon --once
```

持久切回原采集来源时，停止设备监控后执行：

```sh
bash mon-sensors-plugin-0.12.12.run --app /root/ocrun --backend legacy
```

`auto` 仅在主动选择时启用：发现 sckocp 后选择它，否则使用旧来源。已选择 sckocp 后，授权拒绝、格式错误或超时都明确失败，不转用其它硬件来源。运行中的监控进程不会因配置改变而切换，需重新启动。

默认原生格式为 `v1`。已有 v2 扩展的设备可显式选择 `SCKOCP_FORMAT=v2`，或向 `mon-sensors-plugin` 传 `--format v2`。两种格式不自动互相回退，也不允许追加到另一种格式的旧日志中；切换时创建新任务日志。新版 Agent 和旧 `ocrun.sckocp` 包装入口仍使用 v2，本次不改变它们的约定。

默认采样窗口 1 秒、单次超时 20 秒，可用 `--sample-window`、`--timeout` 调整。位置参数仍是监控周期和日志路径；`--once` 只读一次，`--task` 指定任务标签。

0.12.8 对 `timeout` 和 `collection_failed` 默认最多连续重试 2 次，间隔 1 秒。每个失败样本照常记录空指标及错误状态，成功后重新计算重试次数；持续失败则非零退出。可用 `--retries 0` 恢复失败即退出，`--retries` 范围 0–5，`--retry-delay` 范围 0.1–30 秒。`--once` 永远只调用一次；授权、权限、程序信任、格式和配置错误不重试。等待期间可正常停止，不使用旧样本填补失败时段。

## 日志、报表与旧服务器兼容边界

`.mon` 保留两行说明及原有 11 列：类型、时间、当前任务、系统运行时长、1 分钟负载、主频、CPU 温度、VRM 温度、CPU 功耗、整机功耗、风扇转速。

- 原版 v1：类型为 `sckocp-v1`，频率是原版返回核心的 `mhz` 均值，CPU 温度是返回插槽的 `temp_max_c` 最大值，CPU 功耗是返回插槽的 `pkg_w` 总和；任一参与项缺失时该汇总为空。不宣称返回项覆盖所有实际硬件。
- 原版没有 AMD 温度、VRM 温度、PSU 整机功耗或风扇转速时，相应列为空，不补零，也不把 CPU 功耗当作整机功耗。
- 原版没有逐指标有效性和读数年龄。原程序报告的零保留为零，但它也可能表示读取失败。日志首行和 JSON 附件明确标注 `reported-validity-and-age-unknown`，这些数值用于查看，不是硬件合格或温度保护的证据。
- 显式 v2：保留逐指标有效性和年龄检查；过期、不完整的汇总为空，Tctl 不替代物理温度，频率仍为物理核心活动频率均值。

每次接口完整响应和状态另存为 `<日志名>.sckocp.jsonl`。授权失效或采集失败写入空指标行及固定错误状态，不使用旧读数；授权失败立即非零退出，暂时故障按上面的有限重试策略处理。`oct push` 原有的整个日志目录上传会一同传输 `.mon`、JSON 附件和设备生成的 `.xlsx`，服务器作为原来的接收端使用即可。

日志和 JSON 附件必须是当前用户所有的普通单链接文件，拒绝软/硬链接、特殊权限及组/其他用户可写文件。符合原监控格式的已有日志在打开后收紧为 `0600`；格式不符的文件不追加、不修改权限。这意味着 root 采集的文件应由 root 运行原上传命令；普通用户不能直接读取。程序路径的信任检查见 [API 安全要求](SCKOCP-API.md)。

**未修改的服务器分析器可以解析这 11 列，但其 Excel 汇总对全为空的列仍有旧缺陷。** 本包只修正设备端的分析器；需要报表时在设备执行 `oct analyse` 再 `oct push`，服务器查看收到的工作簿。不要将此描述成旧服务器重新生成 Excel 的缺陷也已修复。现有服务器不会自动展示新增 JSON 中的逐核心数据。

旧 `ocb` 不会因为采集失败自动停止压测。本适配保持旧调度行为，没有给旧系统新增安全保护保证。

## 设备端改动和恢复

安装器先识别已有脚本并备份到 `.mon-sensors-backups/`，仅对设备端作以下修改：

- 安装新命令 `mon-sensors-plugin` 和独立模块目录 `mon-sensors-plugin.d/`，不覆盖原命令名。
- `mon-sensors` 增加来源分派，选用 sckocp 时转调 `mon-sensors-plugin`，使用原采集方式时继续运行原主板函数。
- `ocb` 的监控进程检测同时识别连字符和下划线名称。
- `oct mon` 引用路径，`oct killm` 按本应用实际采集路径停止进程并清理采集子进程；日志上传函数保持原样。
- `py/mon-analyse-log.py` 保留全为空的传感器列，支持原 `oct analyse` 的两个参数。
- 增加可选的 `.mon-sensors-backend` 配置文件。

识别不出的脚本或修改过的接入块会拒绝覆盖；重复安装幂等，失败会尝试恢复原脚本、插件入口、采集模块及设备配置。可升级之前未自行修改的 v1/v2/v3 接入块，包括 0.12.5。原有 `sckocp-collector/` 作为旧代码保留，新接入路径不再调用它。设备端以后若执行 `ocsync` 覆盖脚本，需在停止监控后重新应用插件包；本包不修改服务器发布的原工具集。

安装与实时硬件采集按 root 使用；接口和插件不会自行提权。查看帮助不需要 root，但调用者需有权访问安装目录。普通用户不能写入插件、接口或 sckocp 的程序目录；停止 root 启动的监控也需要相应权限。

## 验证范围

测试和打包仅在 GitHub Actions 云端 Linux 进行。验证范围包括独立命令与模块分发、旧接入块迁移、原命令启停、原版 Intel/AMD 数据、授权拒绝、显式来源选择、持久选择、日志锁、安装回退、设备 Excel 和原 rsync 上传。实际 sckocp 授权链使用临时签名和模拟硬件检查，未使用生产密钥。各版本已完成的验证和边界见 [云端验证记录](../archive/validation-20260926/CLOUD-RESULTS.md)；本文不以旧版结果代替本版验证。

客户管理服务器的 `4.18.0-425.3.1.el8.x86_64` 内核未实机验证，本包不在该服务器安装或升级任何组件。真实设备的 CPU、驱动、BMC 和生产授权仍需按实际硬件验收。
