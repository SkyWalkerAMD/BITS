# OCRUN 0.12.8：内网集中压测改造版

**继续使用原版 0730 管理服务器时，请先看 [三个独立分发包](../monitoring-0.3.1/PACKAGES.md) 和 [设备端插件说明](../../monitoring/MON-SENSORS.md)。** 这次交付保留原版 0730 完整归档，插件 `mon-sensors-plugin-0.12.8` 和接口 `sckocp-api-0.3.1` 同时提供 `.run` 自动安装包及 `.tar.gz`。已有管理服务器的系统和 0730 均保持现状，不需要重新安装。设备端新增功能归入独立的 `mon-sensors-plugin` 命令和 `mon_sensors_plugin/` 模块。

本文以下的中心安装、登记设备和 Agent 部署步骤属于新版 OCRUN 架构，**不是给原版 0730 安装采集插件的步骤**。

中心服务器使用 Ubuntu 22.04；客户端面向 x86_64 的 CentOS 7.9、Rocky 8/9/10 和 Ubuntu 20.04/22.04/24.04/26.04 LTS。客户端只依赖 Python 3.6+ 标准库，避免在旧系统上安装一整套 Python 第三方运行库。

这是从 `0730` 工具集整理出的独立改造版。保留原包中的压测工具，重新实现任务调度、常驻客户端、采集与数据汇总。原始压缩包及分析目录中的源码均未修改。新旧任务协议不兼容：旧 `ocb`、`oct`、任务管理菜单、`ocdog` 和旧 Redis 数据库不能与新版客户端混用；本版使用独立 Redis 实例和 `ocrun:h:<设备编号>:` 键空间。

## 已实现

- Ubuntu 22.04 中心服务器安装脚本，可先生成配置供检查。
- 每台设备独立的 Redis ACL 用户、上传凭据和日志模块。
- systemd 常驻客户端，空闲后继续等待任务；默认不自动关机、加电或重置。
- 原子领取及确认任务，保留 pending/running/completed/failed/interrupted/cancelled/timed_out 状态和历史结果。
- 本地单实例锁、中心设备身份锁，防止复制配置后两台设备同时使用同一身份。
- 进程组管理，只终止本任务启动的负载；停止服务时保留中断记录。
- 本地结果日志与待确认记录；中心不可用时稍后补传，不自动重跑压测。
- IPMI 按传感器名称解析；turbostat 按列名解析；coretemp/k10temp/zenpower 的 CPU 温度回退。
- 缺失读数保留 null，风扇逐项存储，记录数据来源、采集错误和采集耗时。
- CSV 汇总有效样本数、有效比例、平均值、最小值和最大值，可在 Excel 中打开。

0.12.8 与独立的 `sckocp-api 0.3.1` 共用采集实现。独立接口默认直接读取原版 1.1.0 的 `mon --json`，不升级、不打补丁、不改 sckocp 的激活流程，供客户管理端单独调用；详见 [通用本地接口](../../monitoring/SCKOCP-API.md)。0730 设备端新增的 `mon-sensors-plugin` 支持原版 v1 和持久选择，沿用旧日志与上报；原 `mon-sensors` 保留原采集功能，仅在选用插件时转调独立命令。管理服务器系统及 0730 无需升级。新版 Agent 和 `ocrun.sckocp` 兼容入口仍要求 v2；见 [sckocp 接入](SCKOCP.md)。本地保护、验收、升级回退和日志策略见 [运行策略](OPERATIONS.md)，设备分组与批次控制见 [批量管理](FLEET.md)。

`completed` 只表示程序按预定方式结束，**不等于硬件验收通过**。`verdict` 为 `passed`、`failed` 或 `insufficient_data`；只有显式配置的验收条件满足且数据充分时才可通过，范围仅限该配置。默认没有完整工具验收证据，结论为数据不足，不自动作硬件合格判定。

## 目录与架构

```text
中心：Ubuntu 22.04
  Redis 6380       独立实例，任务、心跳、设备身份锁，AOF 持久化
  rsync 1873       每台设备一个只允许上传的日志模块
  HTTP 8080        发布客户端安装包和工具包
  ocrun-admin     登记设备、下发任务、查看状态、取消任务

客户端：各受支持 Linux 系统
  /opt/ocrun/current         当前 Python 运行代码
  /opt/ocrun/tools           从原包整理的压测工具
  /etc/ocrun/agent.json      设备配置与凭据（0600）
  /var/lib/ocrun-agent       锁、本地待确认结果
  /var/log/ocrun/<任务ID>    配置、原始输出、采集数据、结果
```

HTTP 与 rsync 接入限制在安装时指定的内网网段。Redis 使用设备独立认证及键空间隔离，但默认管理网传输不加密；服务端安装脚本不会修改现有防火墙，部署时需将 6380/1873/8080 端口限制到管理网。Redis 客户端已支持 `tls`、`ca_file` 配置，服务端 TLS 证书配置需按实际环境另行提供。设备 JSON 凭据通过管理员已有的 SSH/SCP 等受控渠道传递，不放入 HTTP 下载目录。

中心与客户端在本版中分开安装，中心不运行压测负载。

## 1. 构建安装包和工具包

GitHub Actions 会在云端 Linux 构建运行包并提供下载附件。需要整理原工具包时，应在持有原始 `0730` 文件的云端 Linux 构建环境执行；打包不运行包内程序：

```bash
python3 build.py --archive /path/to/0730
```

产物位于源码目录的 `dist/`：

- `ocrun-runtime-0.12.8.tar.gz`：新版中心与客户端运行代码、安装器及本文档，不用于升级现有 0730 服务器。
- `sckocp-api-0.3.1.tar.gz`：原版零修改的第三方本地接口，包含命令、Python SDK、文档及示例，不包含 ocrun 或 sckocp。
- `ocrun-sckocp-api-0.12.8.tar.gz`：保留 v2 约定的兼容接口包装模块与说明，不修改旧系统。
- `mon-sensors-plugin-0.12.8.tar.gz`：0730 设备端的独立采集插件、安装器及独立 API 核心，不包含 `ocrun/` 业务代码。自动发现的新装设备启用 sckocp；显式指定目录时省略来源参数则保留原选择。
- `mon-sensors-plugin-0.12.8.run` / `sckocp-api-0.3.1.run`：离线自动解包安装；支持检查、重复安装和升级，不部署管理服务器或原生 sckocp。
- `ocrun-tools-0.12.8.tar.gz`：从原包选择的压测工具，去掉重复的 bina/binorig、旧调度程序和无关工具。
- `manifest.json`：大小、SHA-256、版本及工具包内容范围。

默认工具包包括原包中的 P95 各模式、BC、MLC、MBW、PTU、Sysjitter。原包的 Stress/Stress-ng 入口是指向原机器系统目录的绝对软链接，打包时会跳过；这些工具使用被测系统自己安装的版本。另提供不依赖第三方二进制的 `cpu-burn` 基础 CPU 负载，供跨系统验证任务链路，它不等同于 P95/Stress-ng 或标准性能基准。原包没有已展开的 UnixBench 工具目录；该任务只有在管理员另外准备好对应工具目录后才能执行。大型 CPU2017 默认不分发，需要时构建：

```bash
python3 build.py --archive /path/to/0730 --include-spec --output dist-full
```

工具来自原包，**没有在当前 Windows 环境下重编译或执行**。每个系统仍需验证工具架构、动态库、CPU 指令集和内核支持。缺少工具时任务明确失败，原始错误输出会被保留。

## 2. 安装中心服务器

将运行包放到 Ubuntu 22.04 并解压。以下 `192.168.50.10` 和 `192.168.50.0/24` 是示例，替换为实际中心地址和设备所在网段。

```bash
mkdir ocrun-runtime
tar -xzf ocrun-runtime-0.12.8.tar.gz -C ocrun-runtime
cd ocrun-runtime

# 可选：仅生成配置，不安装软件、不启动服务。
bash install-server.sh --address 192.168.50.10 --network 192.168.50.0/24 --render-only ./rendered

# 正式安装：需要可用的 Ubuntu 软件源。
sudo bash install-server.sh --address 192.168.50.10 --network 192.168.50.0/24
```

离线中心服务器可以先安装 python3、redis-server、rsync、nginx 系统包，再使用 `--skip-deps` 跳过在线包安装。生成管理员配置 `/etc/ocrun/server.json` 和设备登记目录 `/etc/ocrun/enrollments/`。Redis 数据位于 `/var/lib/ocrun-redis/`，日志位于 `/srv/ocrun/logs/`。备份这些目录及 `/etc/ocrun/`；备份文件包含凭据，应保持访问权限。

安装器会发布客户端包到：

```text
http://192.168.50.10:8080/releases/ocrun-client-0.12.8.tar.gz
```

将构建出的工具包和 `manifest.json` 放到 `/srv/ocrun/public/tools/`，确保文件允许读取（0644）。不向该目录放置 `server.json`、设备配置或 BMC 密码。

## 3. 登记设备并安装客户端

在中心执行：

```bash
sudo ocrun-admin enroll pc001 --output ./pc001.json
```

设备编号由管理员分配，允许字母、数字、下划线和连字符，最多 64 位。每台设备使用不同编号；不要将一份 `agent.json` 复制给整批机器。已登记设备的配置保存在 `/etc/ocrun/enrollments/pc001.json`，重复登记不会覆盖凭据。

将客户端包、`pc001.json`、工具包传给 pc001。客户端已经有 Linux 时执行：

```bash
mkdir ocrun-client
tar -xzf ocrun-client-0.12.8.tar.gz -C ocrun-client
cd ocrun-client
sudo bash install-client.sh --config ../pc001.json \
  --tools ../ocrun-tools-0.12.8.tar.gz \
  --tools-sha256 '<从管理员提供的 manifest.json 取得的 tools.sha256>'
```

只有使用内置 `cpu-burn` 或系统自带 `stress-ng` 等工具时，可以省略 `--tools`。完全离线环境先在对应系统镜像中准备依赖，然后加 `--skip-deps`。该选项不会跳过系统支持检查。

CentOS 7 的仓库已经归档，需由管理员准备可用的归档源或内网镜像；安装器不会擅自替换软件源。Ubuntu 使用与当前内核匹配的 linux-tools；自定义内核需提供自己的 turbostat 工具。Rocky 10 等系统还需满足其硬件要求。

旧 `ocb/ocdog/oc-watchdog` 应先停止并移除自启动。本安装器检测到旧 rc.local 条目会停止安装。不能让新旧调度程序同时控制同一设备。

检查服务与采集：

```bash
systemctl status ocrun-agent
journalctl -u ocrun-agent -n 100 --no-pager
sudo env PYTHONPATH=/opt/ocrun/current python3 -m ocrun.agent --check
```

`--check` 只采集资产信息和一组传感器样本，不启动压力测试。安装程序不会改变 BIOS、调频策略、CPU 隔离、引导项或电源状态。

## 4. 下发与管理任务

在中心执行：

```bash
# 先用小规模、短时任务验证完整链路。
sudo ocrun-admin enqueue pc001 cpu-burn --seconds 60 --threads 4
sudo ocrun-admin status pc001

# 获取全部已登记设备的心跳及任务状态。
sudo ocrun-admin status

# 排队任务原子取消；运行任务客户端默认每两秒尝试检查取消请求。
sudo ocrun-admin cancel pc001 TASK_ID
```

支持设备组、任务模板、有并发限制和启动间隔的批次、暂停/恢复领取，以及 HTML 设备总览与 CSV 导出。HTML 为只读快照，需要重新生成；批次控制器需要持续运行或恢复。详见 [批量管理](FLEET.md)。

持续负载：`cpu-burn`、`stress`、`stress-ng`、`p95-no_m1/m2/m4`、`p95-avx_m1/m2/m4`、`p95-fma3_m1/m2/m4`、`p95-avx512_m1/m2/m4`、`bcfi`、`bcfd`、`ptu`。`--seconds` 为运行时长；持续负载提前退出会被标为失败。P95 使用各工具目录配置，提交不生效的 `--threads` 会明确拒绝。`stress-ng` 启用计算校验，并记录统计输出。

单次基准：`mlc`、`mbw`、`bcr`、`sysjitter`、`unixbench`、`cpu2017`。这些任务的 `--seconds` 是超时上限，正常退出码为 0 才记为 completed。Sysjitter 的内部测试时间是 10 秒，应给更长的任务超时。CPU2017 使用包内配置，正式运行前需核对副本数、编译器和该工具自身环境。

`mbw` 可加 `--memory-mb`；启动前检查三个缓冲区是否超过可用内存。支持线程参数的任务会检查本机逻辑 CPU 数。任务参数允许表固定，不接受任意 Shell 命令。

## 5. 采集设置和故障处理

默认每 5 秒尝试采样。turbostat 与 IPMI 并行采集、分别记录数据年龄；过期读数置空，保留 CPU 各温度来源，Tctl 控制温度与物理温度分开。预检后复用采集器，慢 BMC 不再使每轮串行等待两个超时。默认必须能读到 CPU 温度才启动压测，连续三次缺失必需指标、严重 IPMI 告警、采集停滞或日志磁盘空间不足都会停止本任务。数值保护阈值由管理员按机型配置；任务不能放宽客户端已有保护规则。

修改 `/etc/ocrun/agent.json` 后需重启服务。可以添加主板的名称映射，例如：

```json
{
  "required_metrics": ["cpu_temperature_c"],
  "minimum_free_mb": 256,
  "sensors": {
    "mapping": {
      "cpu_temperature": ["CPU Package Temp", "CPU0_TEMP"],
      "vrm_temperature": ["VRM Temperature"],
      "psu_power": ["PSU2 Power In"]
    }
  }
}
```

将这些字段合并进现有配置，不要删除设备凭据。`psu_power` 应填写实际接入的电源传感器名称；未配置时会寻找所有匹配的 PSU 输入功率，任何一项缺失都会把总功率记为 null，避免低估。

默认访问本机 IPMI。需要远程 BMC 时，在 `sensors.ipmi` 中配置 `host`、`username`、`password_file`，密码文件权限设为 0600。密码通过环境变量传给 ipmitool，不出现在命令行参数中。

客户端默认每 15 秒续报心跳；中心失联超过 120 秒会停止当前负载并保留 interrupted 结果。设备重新启动后将上次未确认执行标为 interrupted，不自动重复运行任务。只有完成确认后才继续领取下一项。

如果设备永久损坏而 running 记录仍在，管理员先确认负载已经停止，再执行：

```bash
sudo ocrun-admin resolve-interrupted pc001 TASK_ID --confirmed-stopped
```

本版不自动调用 IPMI 重置。旧实现中“SSH 不通就重置”及失败返回成功的问题没有被带入新版；自动电源恢复需要后续基于实际管理网络和恢复规则单独验证。

## 6. 日志、报表和升级

日志本地保存，每 60 秒尝试增量上传到中心：

```text
/srv/ocrun/logs/pc001/<任务ID>/
  task.json           参数及领取记录
  preflight.json      启动前传感器样本
  workload.log        工具标准输出和错误输出
  metrics.jsonl       按时间采样的原始结构化记录
  result.json         结束原因、退出码、用时和状态
  execution.json      生效的命令、保护和验收条件、设备信息
  manifest.json       封存文件列表、大小和 SHA-256
  work/               P95 的独立工作目录及输出（使用 P95 时）
```

任务结束会触发上传；封存日志使用校验和传输，中心 `ocrun-log-verifier` 每 30 秒验证清单后确认收齐。已确认任务跳过重复传输。任务结束、文件传输成功和中心校验完成分别记录。默认保留客户端所有日志；显式配置 `local_retention_days` 后，也只清理中心已确认且本地仍匹配清单的历史任务。中心日志与 Redis 数据不自动删除，仍需部署备份与容量监控。

生成新 CSV 报表：

```bash
sudo env PYTHONPATH=/opt/ocrun/current python3 -m ocrun.report \
  /srv/ocrun/logs/pc001/TASK_ID/metrics.jsonl ./pc001-TASK_ID.csv
```

输出文件已存在时会拒绝覆盖。新报表输入是 JSONL；原包的 `.mon` 转 Excel 脚本不适用于此格式。

客户端升级：中心执行 `ocrun-admin pause pc001`，等待现有任务及结果确认结束，再在客户端从新版源码目录执行 `sudo bash install-client.sh --upgrade`。安装器先暂存校验，再切换版本；服务激活失败恢复旧代码与配置，保留日志。检查服务和采集正常后，在中心执行 `ocrun-admin resume pc001`。中心安装失败可用相同地址和网段重试，保留已生成凭据；现有中心的跨版本升级仍需维护窗口和备份，不支持直接覆盖安装。

## 7. 验证状态

全部验证在 GitHub 托管 Linux 运行：逻辑与进程组测试、真实 Redis/nginx/rsync/systemd 集成、安装失败重试和升级回退、Python 3.6 实际运行，以及可选的全部目标发行版依赖安装与运行模块导入。运行包同时构建并校验。最新提交、测试数量、执行链接和范围见 [云端验证结果](../validation-20260926/CLOUD-RESULTS.md)，工作流说明见 [云端验证](../../development/CLOUD.md) 和 [各作业范围](../../../ci/README.md)。

真实服务联调使用模拟传感器数据和短时单线程基础负载，不代表实际主板验收。发行版容器共享宿主机内核，不能替代各系统自己的内核、BMC、第三方工具二进制与硬件组合测试。Python 3.6 容器检查全部模块导入及基本运行行为，不等于在 3.6 上执行了全部第三方测试库。

0.10 构建的旧工具包仍可通过可信 SHA-256 与新版运行代码配合使用，工具包文件名不影响安装校验；云端运行包附件本身不包含原始 0730 工具包。

先选一台有代表性的设备跑通安装、采集、短时任务、取消、断网、服务重启和回传，再按系统/内核/主板建立验收矩阵。本版不涉及 PXE 装机；被测设备应先安装好 Linux。
