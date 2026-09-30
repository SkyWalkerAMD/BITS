# OCRUN 0.11 运行策略

## 本地保护

在客户端配置中合并 `protection` 字段；不要覆盖设备编号和凭据。默认设置：

```json
{
  "protection": {
    "stop_on_ipmi_critical": true,
    "missing_samples": 3,
    "max_metric_age_seconds": 30,
    "max_sample_gap_seconds": 30,
    "limits": {}
  },
  "cancel_poll_seconds": 2,
  "local_retention_days": null
}
```

`limits` 以指标名为键，每条可设置 `min`、`max`、`critical_min`、`critical_max` 和 `consecutive`（默认 3）。例如 CPU 温度使用 `cpu_temperature_c`，指定风扇使用 `fan:FAN1`，电源功率使用 `system_watts`。阈值应由对应主板/CPU/电源的验收规范确定，本项目不提供适用于所有机器的温度数值。配置了限值的指标也会成为必需指标，不能用无读数绕过保护。

普通界限需要连续新读数超限；严重界限或 IPMI `cr`/`nr` 告警会触发本地停止。预检时已经超限则拒绝启动。`nc` 告警保留在数据中，不默认当作严重故障。规则触发后锁定本次失败结论，不自动重启负载。控制器连接失败不阻止采集线程发出本地停止信号。软件采样/进程调度存在延迟，不能替代硬件保护。

任务内可附加保护规则，但只能收紧客户端已有上限、下限、缺失容忍时间和次数，不能关闭客户端已有严重告警保护。取消检查默认间隔 2 秒，网络超时仍会影响远程取消延迟。

## 采样与温度含义

每个指标包含来源、`observed_at`、`age_seconds` 和 `stale`。默认首次采样最多等待 8.5 秒；复用采集器后的每次采样最多等待后台源 0.5 秒，然后读取本机 hwmon。IPMI 默认每 10 秒请求，失败后等待 30 秒再试；IPMI/turbostat 默认过期时间分别为 20/10 秒。配置在 `sensors` 下，具体名称见源码 `Collector.__init__`。

`cpu_temperatures_c` 保存各温度来源；本地保护选取满足新鲜度要求的最高物理温度。AMD `Tdie` 与用于风扇控制的 `Tctl` 分开记录。只有 Tctl 的机器应明确选择 `cpu_control_temperature_c` 作为必需指标，并配置对应机型的控制温度限值；不能直接套用芯片物理温度阈值。AMD 的 `hwmonN` 标识不承诺等同于物理插槽编号。[Linux k10temp 文档](https://docs.kernel.org/hwmon/k10temp.html)

## 验收结论

执行状态和验收结论相互独立。`completed` 表示执行结束；`verdict` 为 `passed`、`failed` 或 `insufficient_data`。历史 0.10 任务的 `not_evaluated` 记录不重写。

下面是一个只检查 CPU 忙碌比例的**示例策略**，只表示满足所列条件，不表示硬件综合验收合格。保存到 JSON 文件后，通过 `enqueue --profile` 或批次模板使用：

```json
{
  "acceptance": {
    "name": "cpu-load-observation-example",
    "metrics": {"cpu_busy_percent": {"min": 80}},
    "minimum_samples": 10,
    "minimum_valid_ratio": 0.95,
    "minimum_in_range_ratio": 0.95,
    "warmup_seconds": 10,
    "minimum_duration_seconds": 60,
    "max_sample_gap_seconds": 15,
    "require_tool_verification": false
  }
}
```

数据缺失、过期、采样间隔过长、记录损坏、样本不足、执行中断均不能通过。有效读数中满足范围的比例低于要求记为失败。结果记录 `acceptance.scope=configured_profile_only` 和每项证据。

默认 `require_tool_verification=true`。当前能识别 P95 的明显计算错误及 stress-ng 的错误输出，运行中发现会停测；尚未实现所有工具的完整工作线程数量、全部计算验证和完成证明，因此默认会给出数据不足。管理员只有在明确接受“配置中的监测条件通过”这一较窄范围时，才关闭该要求。完整的 ECC/MCE、各工具解析与实际主板验收仍需后续补齐。

## 日志传输和保留

客户端先写结果，再生成 `manifest.json`。中心校验每个文件的 SHA-256 与大小，且任务已经进入终态后，在对应设备的 Redis 空间记录 `log_ack:<任务ID>`。上传失败或等待中心校验时保留本地文件。封存文件使用 rsync `--checksum`，避免同大小、同时间戳的损坏文件在重试时一直被跳过。

默认不删除客户端数据。配置 `local_retention_days` 为至少 1 的天数后，仅删除达到保留时间、中心确认清单一致、本地复核仍一致且未运行的任务目录。此确认是正常系统内的数据完整性机制，不是针对被攻陷设备的密码学证明。中心原始日志、任务历史和批次文件均保留；生产部署需安排中心备份、磁盘容量告警及归档。不要把含凭据的配置和 Redis 备份放到公开下载目录。

发生崩溃时，客户端恢复会把未完成任务记为中断，补齐结果清单并重传，不自动再次执行负载。

## 验证范围

自动化验证只在云端 Linux 执行，包括模拟单元测试、真实 Redis/rsync/nginx/systemd 安装和恢复、客户端安装/升级失败恢复，以及实际 Python 3.6 解释器兼容性检查。发行版容器只覆盖软件包安装/用户态能力，共享云端宿主机内核；不代表相应内核、主板、BMC 或原工具二进制通过验收。最新执行证据见 `CLOUD-RESULTS.md`。
