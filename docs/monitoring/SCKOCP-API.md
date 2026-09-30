# sckocp 通用本地监控接口 v1

接口工具版本为 0.3.2，供第三方软件在安装了 sckocp 的同一台 Linux 设备上调用。独立分发包为 `sckocp-api-0.3.2.tar.gz`，不包含 ocrun、sckocp 本体、原生补丁或授权加固草案，不安装服务，不监听端口。默认直接适配原版 1.1.0/1.2.0 的 v1 JSON，无需升级或给 sckocp 打补丁。本版加强可执行文件信任检查、解析边界和失败处理，公共响应字段及默认格式保留。

客户管理端、此接口、sckocp 是独立组件。管理端在设备上调用此接口；管理端位于另一台机器时，由它自己的设备采集程序调用本地接口，再沿已有通道上报。接口的安装、升级和移除不修改 sckocp 的程序、持久配置、激活文件或服务。

使用原版 0730 OCRUN 时，设备端可安装独立的 `mon-sensors-plugin-0.12.12.tar.gz`；该插件已带上本接口的核心模块，无需重复安装此包。原版管理服务器系统及 OCRUN 均保持现状。此 API 包供其他软件独立调用，不包含 OCRUN 设备端插件或管理端。

## 使用条件与授权

- Python 3.6 或更高版本，仅使用标准库。
- sckocp 已安装并具备原有 `mon --json` 输出；用户提供的原始 1.1.0 和 1.2.0 支持此命令。
- 被监控设备必须通过产品现有激活、机器绑定及平台登记校验，并满足原有驱动和权限要求。目前原生硬件读取通常需要 root 权限。

每次默认采集启动 `sckocp mon --json`，由原生程序先检查授权再采集，接口工具不以“曾经激活”标记替代实际校验。沿用产品现有的已签名租约及续期规则，不要求管理端每次查看都联网验证。有效租约可按产品规则离线使用；无法获得有效租约时拒绝输出监控数据。接口层不读取、复制、修改激活码、租约或平台凭证，不缓存授权结论或旧读数，授权失败时不改用其它硬件采集方式。

## 0.3.2 安全加固及逆向边界

接口以 Python 源码交付，拿到包的人可以阅读；这不等于拿到激活私钥。编译、混淆和 `.pyc` 都不能保证阻止拥有设备 root 权限的人逆向、改程序或伪造结果。本版不增加“接口自己的激活判断”，不承诺纯本机防破解，也不改变 sckocp 的现有授权与离线规则。完整审查范围和限制见[接口安全说明](../security/API-SECURITY.md)。

通过安装器创建的 `sckocp-api` 命令增加以下保护：

- 在导入任何接口模块前，重新核对路径、属主、权限、文件类型、链接数及 SHA-256。指纹固定在安装时生成的命令中，仅改本地 manifest 不能授权新的模块。
- 模块全部通过后，执行已经读取并核验的源码字节，避免检查后路径替换；不从 `.pyc` 缓存加载接口代码。`-B` 本身只禁止写缓存，不能替代此检查。
- 使用 `-I -S -B`，排除调用目录、用户 Python 环境以及额外 site 启动钩子；子进程仍沿用环境变量白名单。
- CLI 将普通文件形式的 core dump 上限设为 0、umask 设为 077。管道式系统崩溃收集器与 root 调试属于系统管理边界，不能据此宣称内存无法被读取。
- 采集成功退出后也清理同一隔离会话里的辅助进程；保留子进程退出身份直至清理完成，避免先回收 PID 再发送清理信号。

核验不通过时返回 `integrity_error`、`data:null`、退出码 1，不输出被改文件的内容、私有路径或堆栈。应保留现场并核对安装来源；不要通过放宽权限或修改清单解决问题。

SDK 的 `import sckocp_api` 和直接运行解压目录属于宿主软件的代码加载边界，不经过安装命令内的核验器。SDK 使用者需维护可信代码目录、解释器和导入环境。已有 OCRUN 插件携带独立副本，单独升级全局 API 命令不会自动替换插件中的模块；采集插件 0.12.12 包含本次共享采集进程清理修复，启动命令的源码核验仅用于独立 API 的管理安装。

SHA-256 是完整性核对，不是发布者数字签名。核验器与其指纹一起被 root 改写、系统解释器或标准库被控制时，本地检查不再提供独立的可信证明。接口包不包含 sckocp C 源码、授权签名器、私钥、激活数据或用户的草案。

本机调用没有新增网络监听；sckocp 本身的授权续期仍可能按原机制访问激活服务器。接口仅给采集子进程设置只读模式、关闭自动加载内核模块，不更改产品的持久模式。sckocp 自身在正常授权检查或续期时可能更新自己的状态文件；“独立”不表示拦截这些原有行为。

## 自动安装与升级

以 root 在需要调用接口的设备上执行，无需手动解压：

```sh
bash sckocp-api-0.3.2.run
```

默认将接口安装到 `/opt/sckocp-api`，创建 `/usr/local/bin/sckocp-api` 命令。安装后可以从任意工作目录执行 `sckocp-api --help`，或 `sckocp-api --binary /usr/bin/sckocp` 采集。安装器固定所选 Python 路径，命令不依赖调用者的 Python 模块环境；SDK 仍位于安装目录内，不向系统 Python 写入全局包。

重复执行会检查已有安装；版本变化时备份并升级，捕获到安装失败时尝试恢复旧目录和入口。只接管本安装器管理且未被修改的文件；同名第三方命令、手工安装目录或改过的已管理文件会被拒绝覆盖。已手工解压占用 `/opt/sckocp-api` 的用户可指定一个新目录，不会自动删除旧文件。

```sh
# 只检查环境、冲突和安装计划，不改目标目录
bash sckocp-api-0.3.2.run --check
# 选择其他位置；普通用户也可选择自己拥有且权限安全的目录
bash sckocp-api-0.3.2.run --prefix /opt/sckocp-local-api --bin-dir /usr/local/bin
```

安装器离线运行，仅需 Linux、Bash、tar、SHA-256 工具及 Python 3.6+；缺少 Python 时明确提示，不自动访问外网或改系统依赖。API 安装可识别系统 `python3`、部分带版本命令及 `/usr/libexec/platform-python`。安装过程不运行 `sckocp mon`，不验证或变更激活状态，也不启动服务。`.run` 校验内嵌归档后再解包，临时文件结束后清理；该校验只能检测损坏，不是发布者签名。

`.tar.gz` 仍可直接解压使用，或在解压目录运行 `bash install-sckocp-api.sh` 完成上述安装。0730 插件已包含接口核心，安装插件的节点不需要重复运行此安装包。

## 命令行：适用于任意语言

将独立包解压到固定目录，例如 `/opt/sckocp-api`，在被监控设备上执行：

```sh
/opt/sckocp-api/sckocp-api --binary /usr/bin/sckocp --interval 1 --timeout 20
```

也可在解压目录执行 `python3 -m sckocp_api --binary /usr/bin/sckocp`。`--binary` 必须是实际绝对路径；`--interval` 是一次采样的窗口，范围 0.05–60 秒；`--timeout` 是包含授权检查和硬件读取的总时限，范围 0.1–120 秒，且必须大于采样窗口。默认 `--format v1`，直接使用原版；仅在设备已具备扩展格式时主动选择 `--format v2`，接口不会自动探测、升级或打补丁。

安装目录及其中的 Python 文件和指定的 sckocp 程序应由管理员维护，普通用户不可写。接口自身不提权；不要给不可信调用者配置允许任意 `--binary` 参数的 root 执行权限。手动移除时先停止管理端调用，再移除独立安装目录及确认属于该安装的命令入口，无需卸载或重新激活 sckocp。

0.3.1 在执行前逐层检查程序路径：root 调用只接受 root 所有的文件、目录和软链接；普通用户接受自身或 root 所有的路径。拒绝组/其他用户可写的程序或目录、特殊文件以及 setuid/setgid 程序；root 所有的 sticky 临时目录例外，其下路径仍逐项检查。可信软链接可用，目标路径同样需要通过检查。普通用户所有的目录即便包含一个 root 所有的程序，root 调用仍会拒绝。不要用 `chmod 777` 排除部署错误；将程序放入管理员维护的标准安装路径。

程序通过已检查的 Linux 文件描述符执行，避免检查后替换路径重定向到另一个文件；子进程工作目录固定为 `/`，环境变量使用白名单。该检查不验证程序的数字签名，也不能阻止 root 或被信任的同 UID 所有者直接改写程序内容。Python 接口本身和其导入环境也必须可信，这不是向任意用户开放 root 命令的网关。

接口按总期限和采样窗口计算受限的原生 `SCKOCP_TIMEOUT`，避免继承任意调用环境中的联网设置。原生 wget 的重试仍可能消耗时间，因此外层始终保留硬超时和子进程清理；不能将该设置理解为原生联网流程一定会在其内完成。

一次正常接口调用在标准输出写入一行 JSON，然后退出。成功退出码为 0；配置或采集失败为 1；命令行参数用法错误为 2。`--help` 和 `--version` 输出说明文字，不采集。收到 SIGINT/SIGTERM 时清理正在采集的子进程及其同会话辅助进程组，分别以 130/143 退出；中断时不承诺完整 JSON，调用方应先检查退出码和 JSON 完整性。

其他语言启动该命令并解析标准输出即可。使用参数数组传递参数，调用方不需要拼接 shell 命令，也不需要解析终端彩色界面。建议同一设备按顺序采集，避免多个消费者同时重复采样。

## Python 调用

将解压目录加入应用的模块搜索路径，或把 `sckocp_api/` 放入应用的包目录。导入模块不会启动采集。

```python
from sckocp_api import collect

result = collect(binary="/usr/bin/sckocp", interval=1, timeout=20)
if result["status"] == "ok":
    print(result["data"]["schema"])
    for socket in result["data"]["sockets"]:
        print(socket["id"], socket.get("temp_max_c"))
else:
    print(result["status"], result["error"])
```

函数返回字典，不向终端输出。`examples/read-sckocp.py` 是可复用的最小调用示例，可在解压目录执行：

```sh
PYTHONPATH="$PWD" python3 examples/read-sckocp.py --binary /usr/bin/sckocp
```

## 返回约定

| 字段 | 含义 |
| --- | --- |
| `schema` | 固定为 `sckocp-api-v1`，表示此公共接口协议版本 |
| `status` | `ok` 或下面列出的失败状态，调用方应按状态判断，不解析错误文案 |
| `observed_at` | 本次调用完成时的 UTC 时间，精度为毫秒 |
| `data` | 成功时保留所选原生格式：默认 `sckocp-mon-v1`，显式 v2 时为 `sckocp-mon-v2`；失败为 `null` |
| `error` | 成功时为 `null`，失败时为不含原始授权输出的固定错误文案 |

例如，当前授权不允许读取时返回：

```json
{"schema":"sckocp-api-v1","status":"license_denied","observed_at":"2026-09-22T00:00:00.000Z","data":null,"error":"sckocp did not authorize monitoring with the current licence."}
```

这是文档示例，时间不是实际采样时间。失败响应不包含旧读数、激活码、租约、签名或原始错误文本。

## 默认格式：原版 v1

`data` 保留原生 `schema/version/vendor/family/interval_s/sockets/cores`，不把 v1 冒充为 v2。

| 原版输出 | 可读取字段 |
| --- | --- |
| Intel 插槽 | `id`、`tjmax_c`、`temp_max_c`、`vid_v`、`core_mhz`、`base_mhz`、`pkg_w` |
| Intel 核心 | `cpu`、`socket`、`mhz`、`temp_c`、`vid_v`、`c0_pct`、`c6_pct` |
| AMD 插槽 | `id`、`pkg_w`（可能为 `null`） |
| AMD 核心 | `cpu`、`socket`、`mhz`、`c0_pct` |

单位由字段名表达：`_c` 为摄氏度，`_v` 为伏，`_w` 为瓦，`mhz` 为 MHz，`_pct` 为百分比。
这些字段是原程序报告的数值；原版 JSON 没有逐指标有效性或读数年龄，某些硬件读取失败可能表现为零。`status=ok` 只说明原程序成功退出且输出结构合法，不能当作传感器准确性证明，也不适合直接据此自动判断硬件合格。

原版 JSON 不包含终端面板的全部信息，例如 PSU 整机功耗、AMD 温度和 DIMM 温度。接口保留缺失及 `null`，不从其它字段推算或补零。随包示例将 v1 温度标为 `reported`，年龄为未知，不能当成 v2 的已标记有效数据。

## 可选格式：已安装扩展的 v2

显式使用 `--format v2` 或 `collect(native_format="v2")`。只有设备原先已具备该扩展时才可使用；独立接口本身不安装扩展。以下描述仅适用于 v2。

成功时 `data` 保留以下分组：

| 字段 | 内容 |
| --- | --- |
| `schema`, `version`, `extension` | 原生协议、产品版本和可选扩展标识；`extension` 仅供识别实现，客户端不应绑定其字面值 |
| `vendor`, `family` | CPU 厂商与系列 |
| `sampled_at_unix_ms`, `duration_s`, `interval_s` | UTC 采样时间戳、实际采样时长和请求采样窗口 |
| `read_only` | `true`，表示硬件访问只读 |
| `sockets[]` | 插槽 `id`、`metrics` 和 `flags.thermal_throttling` |
| `cores[]` | 物理核心代表逻辑 CPU 的 `cpu`、所属 `socket` 和 `metrics` |
| `system` | PSU 总输入功耗、`psus[]`、电源数量及冗余说明 |
| `notes[]` | 本次数据的解释和降级说明 |

每个指标固定包含 `value`、`unit`、`source`、`status`、`age_s`。`status=ok` 时有有效读数；不可用时 `status=unavailable`，`value/source/age_s` 都是 `null`，`unit` 仍保留。不要把 `null` 转成 0。每次收到结果后还应把本地经过的时间加到 `age_s`，按应用需要判断是否过期。

插槽指标包括 `temperature_c`、`control_temperature_c`、`package_watts`、`dram_watts`、`tjmax_c`、`vid_volts`、`base_mhz`；核心指标包括 `active_mhz`、`c0_percent`、`c6_percent`、`temperature_c`、`vid_volts`；整机指标为 `psu_input_watts`，电源个体指标为 `input_watts`。单位分别为 `C`、`W`、`V`、`MHz`、`%`。传感器或型号尚不支持时保持不可用。

接口成功不等于每项指标都有数值。Tctl 与物理温度分开，C0 驻留率不等于操作系统 CPU 使用率，活动频率不等于跨时间平均主频。PSU 总输入功耗只有完整有效读数时才输出。当前未提供经型号验证的 VID/DRAM 功耗解码，以及 VRM 温度、风扇转速、AMD CCD/FCLK/MCLK、主板电压和 DIMM 温度等指标。

## 失败状态

| `status` | 含义 |
| --- | --- |
| `license_denied` | 当前授权校验拒绝监控，保留原生错误码 10 的含义 |
| `license_unavailable` | 无法获得有效授权租约，对应原生错误码 12 |
| `unkeyed_build` | 产品构建未配置授权验证公钥，对应原生错误码 13 |
| `platform_required` | 未通过产品原有平台登记校验，对应原生错误码 14 |
| `unavailable` | 无法启动指定 sckocp 程序 |
| `unsafe_executable` | 程序或路径的所有者、权限、文件类型不满足信任要求；未执行该程序 |
| `integrity_error` | 管理安装的独立 CLI 在导入接口代码前发现模块损坏或路径/权限异常；未采集 |
| `permission_denied` | 当前用户无法访问或执行指定程序；原生内部硬件/授权错误仍保留原错误分类 |
| `unsupported_schema` | 产品输出与明确选择的 v1/v2 格式不符 |
| `invalid_data` | 原生输出不满足接口数据约定 |
| `output_limit` | 输出超过限制：标准输出 4 MiB、标准错误 64 KiB |
| `timeout` | 整次调用超过时限，已尝试清理采集进程 |
| `invalid_configuration` | 可执行路径或采样参数不符合约定 |
| `unsupported_platform` | 当前不是受支持的 Linux 环境 |
| `collection_failed` | 产品返回其它非零错误，例如驱动或硬件准备不满足要求 |

除字节上限外，JSON 嵌套限制为 32 层，单个数字文本不超过 128 字符；拒绝非有限数、重复字段、未知字段、未知 CPU 厂商、控制/格式字符及 Unicode 孤立代理字符。不返回原生 stdout/stderr 的错误片段。`collect()` 保持一次调用一次返回，不自动重试，不缓存前次授权或数据。

## 版本与接入范围

第三方应依赖 `sckocp-api-v1` 及本文约定的字段，同时检查 `data.schema`，保留对未知失败状态的兜底处理。工具 0.1.0 默认要求 v2；从 0.2.0 起默认读取原版 v1，已有调用者若仍需要原 v2 数据结构，应明确传入 `native_format="v2"` 或 `--format v2`。0.3.1 加强程序路径检查；0.3.2 保留公共协议和默认格式，为管理安装的独立 CLI 增加运行时完整性检查及 `integrity_error` 失败状态。

0730 的可选适配器从 0.12.5 起调用本公共接口，默认支持原版 v1；0.12.6 将新增实现移到独立的 `mon_sensors_plugin/` 模块和 `mon-sensors-plugin` 命令，沿用旧日志与上报方式。原 `mon-sensors` 只在主动选择插件时转调它；插件安装说明随插件包提供。原版值只按报告值展示，质量未知。新版 OCRUN Agent 的验收采集器及 `ocrun.sckocp` 兼容入口仍要求 v2，不把 v1 值用于其保护或验收规则。第三方独立包不含 OCRUN。本版仅提供本地接口。

外置接口沿用 sckocp 现有授权能力，不修复或更改其原有激活机制，也不能阻止掌握 root 权限的人修改程序或伪造管理端展示。安装独立接口无需重发 sckocp。测试与构建在云端 Linux 执行；模拟传感器和临时签名授权不代表生产硬件验收。
# 0.4.0 补充信息与输出边界

新增可选 `sckocp-api --details`。默认 API 基础 JSON 保持兼容；启用时附加 `sckocp-details-v1`，包含固定只读 overview/info 两次授权调用的独立状态、开始和观察时间、解析字段及经过白名单过滤的文本。总超时仍覆盖全部调用，基础授权失败不再读取补充信息。

**内存时序仅输出 Primary 组**：原生 Primary 行及同组 tCWL/tRC（如提供）。Refresh、Secondary、Tertiary、未知分组及其原文不返回、不进入日志或报告。原机 rmal 解锁状态不改变该规则，API 没有 rmal/任意命令/任意参数转发入口。

Pkg、DRAM 功耗、整机 PSU、内存/DIMM 温度、VCCIN、VID、TjMax、CPU stepping、平台和配置进入补充数据。未提供的数值为 null；传感器有效性和读数年龄没有原生证据时仍为未知。CLI 请求 details 且任一补充项失败时退出非零，但返回各部分状态供诊断，不泄露原生错误全文或授权信息。

BITS 自动采集默认每 10 秒补充一次；不把异步补充当作每条基础样本的同步读数。HTML/报告 JSON 显示样本数量和来源，原 `.mon` 及 Excel 11 列不变。详见 [本轮边界](../releases/0.2.4.md)。下面的基础协议继续适用。
