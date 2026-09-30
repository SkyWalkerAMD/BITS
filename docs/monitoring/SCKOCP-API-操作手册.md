# sckocp-api 0.4.0 操作手册

适用文件：`sckocp-api-0.4.0.run` 和 `sckocp-api-0.4.0.tar.gz`。以下命令在客户的 Linux 被监控设备上执行，示例中的程序路径需与设备实际安装一致。

## 1. 安装在哪里，需要什么权限

接口安装在 **sckocp 所在设备**，供同机软件调用；它不是中心管理端，不安装服务、不监听网络端口。远程管理端应通过自己的设备采集程序取得数据。安装 OCRUN 设备插件的节点已包含接口核心；只用于 OCRUN 时，按 [插件操作手册](MON-SENSORS-PLUGIN-操作手册.md) 安装插件即可，无需重复安装独立接口。

- 需要 Linux、Bash、tar、SHA-256 工具及 Python 3.6+，安装器离线运行，不自动安装依赖。
- 默认安装目录需要 root 写权限。下面的默认安装与采集示例均按 root 操作；`--help`、`--version` 本身不需要 root。
- 接口不会提权。普通用户可安装到自己拥有的安全目录，但原生 sckocp 的硬件读取通常仍需要 root；能安装不代表能采集。
- 原版 sckocp 1.1.0/1.2.0 支持默认格式，须完成产品原有激活、机器绑定和平台登记，并具备所需驱动。接口不修改本体或授权文件，不绕过校验。

每次采集都调用原生程序检查当前授权，沿用其签名租约与续期规则；sckocp 自身可能联网续期并更新自己的状态。接口不缓存授权结论或之前的读数。

## 2. 自动安装：推荐流程

将可信渠道取得的 `.run` 文件复制到设备，在该文件所在目录执行：

```bash
# 查看安装器帮助；这些不是采集命令
bash sckocp-api-0.4.0.run --help
# 预检查：不改安装目标，不验证激活或采集硬件
bash sckocp-api-0.4.0.run --check
# 执行安装
bash sckocp-api-0.4.0.run
# 核对命令；这两条不采集
/usr/local/bin/sckocp-api --version
/usr/local/bin/sckocp-api --help
```

默认安装结果：

| 路径 | 用途 |
| --- | --- |
| `/opt/sckocp-api/sckocp_api/` | 接口及 Python SDK 模块 |
| `/opt/sckocp-api/.sckocp-api-install.json` | 安装清单，不要手工修改 |
| `/usr/local/bin/sckocp-api` | 命令入口，固定安装时选定的 Python 和模块目录 |

安装器成功返回退出码 `0`，并输出 JSON 计划/结果；`action` 为 `install`、`upgrade` 或 `unchanged`。安装拒绝或失败返回 `1`，命令行用法错误通常为 `2`。`--check` 下的 `action` 只是计划。`.run` 仍会创建临时解包目录并在结束后清理；其内嵌校验用于发现损坏，不是发布者签名。

安装参数如下；它们传给 `.run` 或解包后的 `install-sckocp-api.sh`，**不要传给采集命令**。

| 参数 | 默认值与说明 |
| --- | --- |
| `-h`, `--help` | 显示安装帮助 |
| `--check` | 只检查环境、目标冲突和安装计划 |
| `--prefix PATH` | 默认 `/opt/sckocp-api`；独立、专用的模块目录 |
| `--bin-dir PATH` | 默认 `/usr/local/bin`；创建 `sckocp-api` 入口的目录 |
| `--source PATH` | 默认自动确定解包目录；高级选项，通常无需指定 |

自定义位置示例，升级时也必须使用同一组路径：

```bash
bash sckocp-api-0.4.0.run --prefix /opt/sckocp-local-api --bin-dir /opt/sckocp-api-bin --check
bash sckocp-api-0.4.0.run --prefix /opt/sckocp-local-api --bin-dir /opt/sckocp-api-bin
/opt/sckocp-api-bin/sckocp-api --version
```

模块目录与命令目录不能相同或互相包含。安装目标路径不能经由软链接，属主和权限必须可信；不要用 `chmod 777` 解决安装错误。安装器没有 `--version`、`--status`、`--uninstall` 或 `--rollback` 参数。

## 3. 使用 tar.gz 安装

`.run` 与 `.tar.gz` 二选一即可。按 root 在压缩包所在目录执行以下命令，解包目录与最终安装目录分开：

```bash
mkdir -p /root/sckocp-api-0.4.0-source
tar -xzf sckocp-api-0.4.0.tar.gz -C /root/sckocp-api-0.4.0-source
bash /root/sckocp-api-0.4.0-source/install-sckocp-api.sh --check
bash /root/sckocp-api-0.4.0-source/install-sckocp-api.sh
```

也可不安装，直接在可信解包目录调用其中的 `./sckocp-api` 或 `python3 -m sckocp_api`。这种方式依赖调用者的 Python 环境，不会自动创建全局命令；正式部署优先使用安装器生成的固定入口。

## 4. 采集命令与全部参数

先确认原生程序实际位置：

```bash
command -v sckocp
# 若结果不是 /usr/bin/sckocp，将下方 --binary 改为实际绝对路径
/usr/local/bin/sckocp-api --binary /usr/bin/sckocp --interval 1 --timeout 20
```

一次调用输出一行 JSON 后退出，没有常驻进程。成功响应的 `schema` 是 `sckocp-api-v1`、`status` 是 `ok`、`data.schema` 默认为 `sckocp-mon-v1`。失败响应的 `data` 是 `null`，`error` 是固定文案，不含原生授权输出。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-h`, `--help` | — | 显示帮助，不采集 |
| `--version` | — | 显示 `sckocp-api 0.4.0`，不采集 |
| `--binary PATH` | `/usr/bin/sckocp` | 原生程序的绝对路径；最长 4096 字符，不接受控制/格式字符 |
| `--format v1\|v2` | `v1` | 原版 1.1.0/1.2.0 使用 v1；v2 仅适用于设备已经具备相应扩展的情况 |
| `--interval SECONDS` | `1` | 单次采样窗口，范围 `0.05–60` 秒；不是两次调用间隔 |
| `--timeout SECONDS` | `20` | 整次调用的总期限，范围 `0.1–120` 秒，且必须大于 `--interval` |

独立接口没有 `--watch`、`--once`、`--retries`、`--log` 或网络监听选项。它始终单次采集，不自动重试；这些选项不能从插件命令照搬过来。

### 保存结果并保留退出码

下面在新建的私有目录保存一次结果，避免覆盖未知文件；不经过管道，因此不会被 `tee`、`cat` 等命令的退出码覆盖：

```bash
(
umask 077
sample_dir=$(mktemp -d /var/tmp/sckocp-api.XXXXXX) || exit 1
if /usr/local/bin/sckocp-api --binary /usr/bin/sckocp --interval 1 --timeout 20 \
    > "$sample_dir/result.json" 2> "$sample_dir/stderr.log"; then
    rc=0
else
    rc=$?
fi
printf '采集退出码=%s，结果目录=%s\n' "$rc" "$sample_dir"
cat "$sample_dir/result.json"
cat "$sample_dir/stderr.log" >&2
exit "$rc"
)
```

应用应先检查 `rc`，再检查 JSON 完整性及 `status`。用法错误或中断可能没有完整 JSON；不要把空输出当成成功。接口本身不创建日志，重定向文件的权限与保留周期由调用者负责。日志不要放到安装目录内。

### 周期采集

下面每次成功后等 5 秒再采集；失败立即停止，保留失败退出码。单次采集耗时不包括在这 5 秒内，因此不是严格每 5 秒一个点。

```bash
(
    while true; do
        if /usr/local/bin/sckocp-api --binary /usr/bin/sckocp --interval 1 --timeout 20; then
            sleep 5
        else
            rc=$?
            printf '采集停止，退出码=%s\n' "$rc" >&2
            exit "$rc"
        fi
    done
)
```

按 `Ctrl+C` 停止。正式接入时由调用方调度，尽量让同一设备串行采集；API 不自带进程守护或定时任务。若应用实现重试，只对 `timeout`、`collection_failed` 设置有限重试；授权、安全、配置错误应停止并处理，不返回旧数据充当新读数。

## 5. Python SDK 调用

自动安装不会向系统 Python 注册全局包，需要显式加入安装目录。下面示例适用于默认安装位置；自定义安装时替换 `sys.path.insert` 的路径。应以满足原生采集权限的用户运行；若设备仅有 `/usr/libexec/platform-python` 等解释器，将第一行的 `python3` 换成实际可用的 Python 3.6+ 命令：

```bash
python3 -I -B - <<'PY'
import json
import sys
sys.path.insert(0, "/opt/sckocp-api")
from sckocp_api import collect

result = collect(binary="/usr/bin/sckocp", interval=1, timeout=20, native_format="v1")
print(json.dumps(result, ensure_ascii=False, allow_nan=False))
if result["status"] != "ok":
    sys.exit(1)
for socket in result["data"]["sockets"]:
    print("socket", socket["id"], "temp_max_c", socket.get("temp_max_c"))
PY
```

`collect()` 返回字典，本身不输出文字、不重试、不缓存；参数与命令行对应，格式参数名为 `native_format`。示例会先输出完整 JSON，再打印插槽温度，供人工查看；程序互通时只传递字典或 JSON。AMD 原版缺少该温度字段，`None` 不代表 0°C。SDK 的导入环境和调用脚本也必须可信。

## 6. 退出码与失败状态

| 接口命令退出码 | 含义 |
| --- | --- |
| `0` | 成功采集；或成功显示帮助/版本 |
| `1` | 返回失败状态，包括配置值越界、授权拒绝或采集失败 |
| `2` | 参数用法错误，如未知参数、无法解析的数字或无效的 `--format` |
| `130` / `143` | SIGINT / SIGTERM 中断；清理采集子进程，不保证完整 JSON |

原生 sckocp 的 `10/12/13/14` 被转换为以下 `status`，不是接口命令的退出码。调用方应识别状态字段，不依赖错误文案。

| `status` | 意义与处理 |
| --- | --- |
| `ok` | 原生程序成功退出且输出结构合法，可读取 `data` |
| `license_denied` | 原生错误码 10；通过产品原有流程处理激活、有效期或机器绑定 |
| `license_unavailable` | 原生错误码 12；无法取得有效租约，检查产品授权服务与原有离线规则 |
| `unkeyed_build` | 原生错误码 13；产品构建缺少验证公钥，联系产品提供方 |
| `platform_required` | 原生错误码 14；按产品原有流程完成平台登记 |
| `unavailable` | 无法启动程序，检查 `--binary`、程序是否存在及加载环境 |
| `unsafe_executable` | 程序或路径不可信，未执行；检查属主、写权限和文件类型 |
| `permission_denied` | 当前用户无法访问或执行程序；检查目录访问权和程序执行权限 |
| `unsupported_schema` | 输出不符合选择的格式；原版设备使用 `--format v1` |
| `invalid_data` | 输出结构、字段或数值不合法；保留版本信息并联系维护者 |
| `output_limit` | 标准输出超过 4 MiB 或标准错误超过 64 KiB |
| `timeout` | 超过总期限，已尝试清理进程；检查授权连接和设备负载，再调整期限 |
| `invalid_configuration` | 路径、采样窗口、期限或格式参数不符合约定 |
| `unsupported_platform` | 运行环境不是受支持的 Linux |
| `collection_failed` | 原生程序其他非零退出；检查原产品的权限、驱动和硬件准备 |

JSON 另有 32 层嵌套、128 字符数字文本限制，并拒绝重复字段、未知字段、非有限数及异常控制字符。所有失败响应的 `data` 都是 `null`，不会包含旧读数。

## 7. 升级、备份及常见排查

升级前暂停调用方，然后运行新版本 `.run --check`，检查通过再去掉 `--check`。重复安装同版本且文件未变会返回 `unchanged`；自定义路径必须继续传相同的 `--prefix` 和 `--bin-dir`。

版本或受管理内容变更时，安装器在原目录旁保留备份：默认形如 `/opt/sckocp-api.backup-随机值` 和 `/usr/local/bin/sckocp-api.backup-随机值`；实际路径见安装结果中的 `backup`、`command_backup`。捕获到安装失败时尝试恢复旧目录和入口；若回退未完成会明确报错并保留备份。恢复应同时处理模块目录和命令入口，由管理员核对路径后操作，没有一键回退或卸载命令。

| 现象 | 检查与处理 |
| --- | --- |
| 找不到 `sckocp-api` 命令 | 用 `/usr/local/bin/sckocp-api --version`；自定义安装改用实际 `--bin-dir`，检查 PATH |
| 缺少 Python | 安装器不自动补依赖；由管理员从系统或内网软件源准备 Python 3.6+ 后重试 |
| `ModuleNotFoundError: sckocp_api` | SDK 尚未加入模块路径；参考第 5 节，不要假设已经安装到全局 site-packages |
| 安装提示 unmanaged / local changes / additional files | 已有手工目录、第三方命令或本地修改；先保留现场并检查差异，可选用全新的模块与命令目录，不强行覆盖 |
| 安装器无法访问上传包解压目录 | root 安装不接管普通用户可修改的源码目录；用可信 `.run` 安装，或由管理员在安全目录重新解包 |
| 采集返回 `unsafe_executable` | root 调用要求程序及完整路径由 root 维护且无组/其他用户写权限；检查 `ls -l /usr/bin/sckocp`，有 `namei` 时可用 `namei -l /usr/bin/sckocp` 逐层查看 |
| 路径可信但仍采集失败 | 普通用户安装不会获得 root 硬件访问权；授权、驱动、平台登记问题按原 sckocp 流程处理 |
| 修改 PATH / PYTHONPATH 后入口仍用旧解释器 | 安装器固定 Python 路径并隔离用户模块环境；升级或变更系统 Python 后，用原安装参数重新检查安装 |

可信的 sckocp 软链接允许使用，其目标仍会逐层检查；安装目录自身的规则更严格，不接受路径软链接。不要向不可信用户开放带任意 `--binary` 的 root 执行权限，也不要通过修改授权文件排除错误。

## 8. 数据范围与接入边界

原版 v1 的 Intel 插槽包含温度、VID、频率和封装功耗等报告值；AMD 插槽主要是封装功耗，核心包含频率和 C0 驻留率。原版 JSON 不包含终端面板的全部信息，如 PSU 整机功耗、AMD 温度和 DIMM 温度。缺失及 `null` 必须保留，不能补成 0。

`status=ok` 表示本次执行与结构检查成功，不表示每个传感器读数准确。原版没有逐指标有效性或读数年龄，某些读取失败可能表现为零；C0 驻留率也不是操作系统 CPU 使用率。当前接口不改变原生采样算法，不应单凭这些值自动验收硬件或执行温度保护。

完整数据字段、v2 可选格式和公共协议见 [SCKOCP-API.md](SCKOCP-API.md)。本包独立于现有 0730 管理服务器，安装不会升级管理端或 sckocp；接口安全检查不能阻止掌握 root 权限的人修改程序。
