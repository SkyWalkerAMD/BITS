# sckocp mon 的 OCRUN v2 可选接入

需要“原版 sckocp 零修改”的独立管理端接口时，使用 [sckocp-api 0.3.1](SCKOCP-API.md)，默认读取原有 `mon --json`。0730 的旧设备端通过独立的 [mon-sensors-plugin 0.12.8](MON-SENSORS.md) 接入此公共接口，管理服务器的系统及 0730 均无需升级。以下说明针对新版 Agent 和 `ocrun.sckocp` 的 v2 约定，包含原生扩展步骤；它不是独立接口或 0730 默认接入的安装前提。

面向第三方的独立入口现在是 `sckocp-api` 命令及 `from sckocp_api import collect`，分发包 `sckocp-api-0.3.1.tar.gz` 不含 ocrun，也不安装网络服务。公共协议、调用示例和授权要求见 [通用本地接口文档](SCKOCP-API.md)。本文保留原生接口与 ocrun 兼容入口的说明。

sckocp 提供独立本机数据接口，监控程序自行选择是否调用。接口不依赖 `mon-sensors`、`oct`、旧主板函数或中心服务。需要在原 `mon-sensors` / `oct mon` 中选择此来源时，可另行安装 [mon-sensors-plugin](MON-SENSORS.md)；原命令默认保留原采集方式。新增代码集中在 `mon_sensors_plugin/` 中，插件包不包含 `ocrun/` 业务模块。

接口采用本机命令调用：输入采样参数，标准输出返回一次 JSON，退出码表明成功或失败，没有新增网络监听端口。调用方负责采样周期及上报。启用后可形成 `sckocp → 接口 → mon-sensors/其他监控客户端 → 中心` 的数据路径；OCRUN 接入默认关闭，启用后使用现有心跳与日志通道。

## 安装原生接口扩展

原始 sckocp 1.1.0 的 `mon --json` 是 v1，输出字段和有效性信息不足。本次增加 v2，保留 v1 行为。将云端产物 `sckocp-1.1.0-mon-v2.patch` 应用到用户提供的 **原始完整 1.1.0 源码目录**，再按 sckocp 原有流程编译与打包。操作均在 Linux 构建服务器完成：

```sh
patch --dry-run -p1 < /path/to/sckocp-1.1.0-mon-v2.patch
patch -p1 < /path/to/sckocp-1.1.0-mon-v2.patch
```

补丁来源及校验值见 `patch-manifest.json`。补丁保留原有激活检查和平台登记检查，不修改产品密钥、授权规则或持久运行模式。每次采集强制本进程硬件只读，并关闭自动加载内核模块；硬件只读不代表文件系统完全无写入，正常授权续租等产品状态文件仍可能更新。因此设备需提前满足原有 sckocp 安装、驱动、权限、激活及平台登记要求。不要把云端临时测试二进制用于部署。

## 独立调用

原生接口可以直接调用。已有程序使用 `python3 -m ocrun.sckocp` 时，可解压兼容包 `ocrun-sckocp-api-0.12.8.tar.gz` 后，在解压目录继续运行。该包含兼容入口、共享的 `sckocp_api` 采集模块和本说明，没有安装器、监控主循环或旧系统脚本；不需要安装 OCRUN 客户端服务。新接入的第三方软件请使用上面的通用接口。

```sh
# 产品原生接口；单次采样，INT 是采样窗口（秒）
INT=1 /usr/bin/sckocp mon --json=v2

# 在独立接口包解压目录执行；已安装 OCRUN 时也可在 /opt/ocrun/current 执行
python3 -m ocrun.sckocp --binary /usr/bin/sckocp --interval 1 --timeout 20
```

Python 调用方也可以在模块可导入时使用 `from ocrun.sckocp import collect`，调用 `collect(binary="/usr/bin/sckocp", interval=1, timeout=20)` 获得同样的结构化结果。解压或导入模块不会启动采集，只有执行接口调用时才读取数据。

原生 v2 不支持 `--watch`；调用方按所需周期重复采集。sckocp 若安装在 `/usr/local/bin/sckocp`，应改为实际绝对路径。包装接口仅依赖 Python 3.6+ 标准库。它使用固定参数、限制输出大小，并在超时后终止采集进程组；不把命令原始错误文本上传，避免包含授权或设备身份信息。

成功时包装输出 `schema=ocrun-sckocp-v1`、`status=ok`、`observed_at`、`data` 和 `error=null`，退出码为 0。`data` 是经过验证的 `sckocp-mon-v2`。单项指标不可用不等于整个调用失败，必须继续检查指标状态。

失败时 `data=null`，退出码为 1。状态区分 `license_denied`、`license_unavailable`、`unkeyed_build`、`platform_required`、`unavailable`、`timeout`、`invalid_data`、`unsupported_schema` 和其他采集失败。旧版产品需要升级接口；包装器不会悄悄回退到 v1 或绕过激活获取产品数据。

## 数据约定

每项指标包含 `value`、`unit`、`source`、`status`、`age_s`。不可用时 `value/source/age_s=null`、`status=unavailable`，数值 0 只有在确实测得时才成立。时间使用 UTC 毫秒 `sampled_at_unix_ms`；`duration_s` 是实际采样窗口，功耗据此计算。`age_s` 包括已知 BMC 缓存年龄。

| 分组 | 当前输出 |
| --- | --- |
| sockets | 插槽 ID、CPU 物理温度、单独的 Tctl、CPU 封装功耗、Intel TjMax 和基准频率、热节流标志 |
| cores | 每个物理核心的代表 CPU ID、所属插槽、活动频率、C0 驻留率、Intel C6 驻留率与核心温度 |
| system | PSU 输入功耗、各电源功耗、数量与冗余描述 |

Intel 温度必须满足传感器有效位；AMD 物理温度只接受 Tdie，多路设备需要明确的插槽绑定。Tctl 不作为 Tdie 的替代。物理核心活动频率取兄弟线程中最高的 APERF/MPERF 比值，C0 取最高驻留率；它们与操作系统 CPU 使用率、跨时间平均频率含义不同，因此使用独立字段。PSU 总功耗要求完整有效读数，部分缺失不输出部分总和。

本版仍未提供经型号验证的 VID 与 DRAM 功耗解码，字段明确不可用。AMD CCD 温度、FCLK/MCLK、主板电压、DIMM 温度等终端面板扩展项尚未导出。冗余描述目前没有可靠采集时间，`redundancy_age_s=null`，只用于展示，不作保护依据。

v2 的可选 BMC 读取最多两次、每次限制 2 秒，避免慢 BMC 阻塞全部 CPU 读数；不复用可能只完成部分读取的旧缓存。慢 BMC 的 PSU 指标会不可用，可继续使用 OCRUN 独立的 IPMI 来源。超过 8 个 PSU 或无法唯一识别电源时，PSU 部分降级为未知并附说明，CPU 数据仍然输出。

## OCRUN 客户端配置

合并以下字段到设备 `/etc/ocrun/agent.json`，保留原有设备 ID 和中心连接凭据，然后重启该客户端服务：

```json
{
  "required_metrics": ["cpu_temperature_c", "sckocp_available"],
  "sensors": {
    "initial_wait_seconds": 25,
    "sckocp": {
      "enabled": true,
      "binary": "/usr/bin/sckocp",
      "interval_seconds": 1,
      "timeout_seconds": 20,
      "poll_interval_seconds": 5,
      "max_age_seconds": 30
    }
  }
}
```

上例要求压测前获得有效温度和经过授权的 sckocp API 响应。`sckocp_available=1` 仅表示接口响应有效；不保证每项硬件指标都可用，需要哪些指标就继续加入 `required_metrics`。如果允许 sckocp 不可用时依靠其他独立传感器继续压测，可不把 `sckocp_available` 列为必需项。采集异常、过期或授权失效时不会伪造成功数据，失败刷新立即清除 sckocp 旧缓存。

后台采集同一来源不会重叠运行。客户端将收到接口响应后经过的时间与指标自身年龄相加，判断过期；过期读数不参与保护或汇总。完整原生数据保存在采样的 `sckocp.data`，同时附上 `source_status.sckocp`。读取原始数据的其他消费者也必须检查这两层年龄，不可把旧快照当作当前值。

压测执行期间，每次采样写入 `metrics.jsonl`；设备约每 15 秒的心跳 `metrics_summary.sckocp` 携带最近的完整接口快照，中心状态查询可以读取。心跳另有独立字段 `cpu_core_active_mean_mhz`、`cpu_core_active_max_mhz`、`cpu_core_c0_mean_percent`、`cpu_core_c0_max_percent`，CSV 汇总和设备总览也展示相关指标。均值及总功耗仅在对应核心或插槽集合完整有效时输出。空闲设备沿用现有行为，不持续采集。

## 验证边界

编译与测试只在 GitHub Actions 云端 Linux 进行。原生测试使用临时授权和模拟硬件，检查接口、缺失值、激活边界及补丁应用；客户端测试检查解析、超时、年龄、缓存失效和上报。实际设备上的 MSR/驱动支持、传感器精度及 BMC 可用性仍需在目标硬件上验收。云端最新结果见 [CLOUD-RESULTS.md](CLOUD-RESULTS.md)。
