# 节点操作与历史优化

BITS / BITS-o 的安装、任务启动、状态和恢复见 [当前简明手册](../deployment/BITS.md)。旧 215 仍使用 occt 下发；一次明确启动处理当前批次，采集与报告自动完成。下面保留历史独立组件的变更记录，不作为 BITS 的逐组件安装步骤。

## 0.2.5 详细机器报告

在原自动收尾中增加 `acceptance_report` 阶段，生成自包含HTML与结构化JSON；含批次开始机器配置、各步实际执行记录、传感器统计、异常和文件追溯。新回执v2验证五份数据文件，旧批次保留v1三份数据，不补写历史配置。报告失败保留待办，重试不重跑任务、不改已封存数据。仍不自动判定硬件合格。

节点root在空闲、无待办时执行 `bash mon-sensors-finish-0.2.5.run --app /root/ocrun --check`，确认后去掉 `--check`。支持已知0.1.0及0.2.0–0.2.4受管版本；用同一安装器 `--rollback --check` 与 `--rollback` 核验后退回先前组件，保留日志。215查看新增回执需独立查看插件0.2.0，不改原控制端程序。完整操作和现场验收见发行根目录 `ACCEPTANCE-REPORT.md`。

以下为历史版本说明，用于理解已完成的修正，不是要求重新执行旧整包升级。

## 0.2.1 系统工具链接修正

现场确认原版stress/stress-ng入口是201:200所有的链接，实际指向root所有的 `/usr/bin/stress` 和 `/usr/bin/stress-ng`。0.2.1在显式adopt-workloads操作中支持这两个系统入口（及 `/bin/` 别名），仍严格检查目标和系统父目录，固定描述符后复制为私有普通文件，不修改原链接或放宽其他数据链接检查。清单增加system_target来源信息。

本次仅升级收尾组件。已安装0.2.0的节点使用随包README-LINK-FIX.md，支持单独回滚收尾到0.2.0；报表保持0.2.0。下文为0.2.0引入的功能和整包升级背景，不应把旧整包回滚用于0.2.1单组件修正。

## 适用范围

现场已验收基线是 Rocky 8.10 / platform-python 3.6.8 / sckocp 1.2.0 / API 0.3.1 / 插件 0.12.9 / 报表 0.1.1 / 收尾 0.1.0。
本轮升级报表与收尾到 0.2.0，API、采集插件、sckocp 和原控制服务器保留。测试证据随发布包附带，云端模拟与硬件现场验收分开记录。

## 问题与改动

| 原问题 | 0.2.0 行为 | 主要源码 |
| --- | --- | --- |
| strss 被原菜单接受，节点取出后才发现问题 | 一次读取全部当前任务，检查允许的项目、时长、工具和磁盘；领取前核对整个剩余队列 | node.py / queue.py |
| LPOP 与删除时长分开，同名项目第二次失去时长 | 原子比较并领取，保留名称对应的时长；本地领取意图先落盘 | queue.py |
| shell 后台启动返回0掩盖负载提前退出 | 独立监督程序记录真实退出码、时长、停止原因和清理结果 | workload.py |
| 按进程名称批量 kill -9 | 核对属于本次会话的进程身份，先TERM，再有界升级；不调用原 oct killa | workload.py |
| 调度被杀后负载/采集遗留 | Linux父进程死亡通知让监督程序清理本轮负载和采集；状态保持待处理 | node.py / workload.py |
| 几天日志超出25万行或512MiB | 流式校验；大报表每25万行拆工作表，完整数据保留，趋势每1000条汇总 | finish.py / bits_core/reporting/streaming.py |
| 缺少磁盘预算 | 启动前按时长与CPU数估算，运行中保留512MiB磁盘余量；触发保护停止负载 | node.py |
| optional Memtest目录缺失刷错误、旧上传返回码掩盖失败 | 可选目录缺失提示跳过，必要rsync错误保留退出码，失败阻止后续关机 | oct-hook.sh / install.py |
| 状态只有大段JSON | status --human 和 status --case；分别记录执行、数据质量、报表、交付 | finish.py |
| 原版本检查可覆盖插件 | 版本不同或获取失败即停在检查，不进行自动同步覆盖；管理文件哈希核对 | hook.sh / install_guard.py |
| 旧版升级难以撤回 | 已识别0.1.0升级、完整备份、--rollback，保留历史数据 | install.py |

## 业务边界

- 一次启动只处理启动时快照中的同一批次；本轮新增/修改队列触发不一致时停止并保留记录，不持续领取空闲任务。
- Redis仍使用原主机映射、IDS、DATES、TASKS及项目时长键。STATUS/CURRENT写入原兼容字段，附加MON_PLUGIN_CLAIM用于防止旧批次状态覆盖新批次。
- 原队列每个项目名只有一个时长键，因此相同名字的多次任务共享最后设置的时长。不同次运行需要不同的时长时，使用已支持的 stress_r2/stress-ng_r2 等不同名字；不声称能恢复原队列没有保存的信息。
- 工作负载上限31天；单批次上限400万样本，每个源文件16GiB。预算超出时在启动前要求拆批次；不静默删记录。较大核心数、网络慢、磁盘小的环境需要更短批次。
- 报表不提供硬件合格结论。v1有效性、读数年龄未知；缺失字段仍为空白。
- 原自动关机逻辑保留。队列、执行、采集或收尾失败时 run_task 非零返回，原 run_main 不进入关机。工作完成后仍按原登录检查和倒计时处理。
- stress、stress-ng、P95及其他允许列表内负载采用受控启动；有限次基准用最大时长限制，提前正常退出可记录finished。各工具实际CPU/内存/配置行为需要相应硬件验证，云端不会运行真实压测。
- stress-ng显式传入配置时长加5秒的原生超时，避免超过一天的任务先被其默认时限结束；独立监督程序仍按配置时长停止。5秒用于第二道时限的清理余量，不延长配置的主计时。

## 操作入口

已有兼容组件的节点，在空闲且旧ocb退出后执行发布包的 `bash update-node.sh --check`、`bash update-node.sh --apply`。安装不会领取任务。

```sh
/root/ocrun/mon-sensors-finish --version
/root/ocrun/mon-sensors-finish check --scheduler
/root/ocrun/mon-sensors-finish status --human
/root/ocrun/mon-sensors-finish start
/root/ocrun/mon-sensors-finish status --case CASE_ID
```

start明确启动一次原ocb，保留初始化等待和关机策略；重复启动会被拒绝。运行日志为 `/root/ocrun/.mon-sensors-finish/scheduler.log`。调用者可用 `tail -n 60` 查看。

只读队列预检（按实际节点/序列号填写）：

```sh
/root/ocrun/mon-sensors-finish preflight \
  --rdb-server 172.20.8.217 --log-server 172.20.8.211 \
  --log-dir /root/log --node K6C-165 --serial 260168795800086
```

原工具常见属主201:200不会被直接作为root受信任程序执行。明确审阅并信任指定原工具时，先查看文件哈希，再复制到私有目录；原工具保持原样：

```sh
/root/ocrun/mon-sensors-finish adopt-workloads --task stress --task stress-ng --check
/root/ocrun/mon-sensors-finish adopt-workloads --task stress --task stress-ng
```

该操作是信任原工具的显式选择，不是来源签名验证。拒绝组/其他用户可写路径、未知软链接、数据硬链接、特殊文件及超出预算的目录；0.2.1只对上述明确的系统程序入口按系统可执行文件规则验证。副本改动会在运行前被检查。其它工具按需添加对应 `--task`，不要批量信任未知程序。

## 异常处理

```sh
# 定向请求当前批次停止，核对boot ID、PID/start time和命令身份。
/root/ocrun/mon-sensors-finish stop --case CASE_ID
# 已封存的报表/上传失败只重试收尾。
/root/ocrun/mon-sensors-finish retry --case CASE_ID
# 未封存的中断批次明确恢复；不会重新运行压测。
/root/ocrun/mon-sensors-finish recover --case CASE_ID --interrupted
# 不完整且无法恢复的材料保留，明确关闭待办，不标记成功。
/root/ocrun/mon-sensors-finish close-incomplete --case CASE_ID --reason '已检查原因和保留材料'
```

领取意图保存在批次JSON的queue_snapshot/claim中。断电或Redis回复丢失时，不能自动判断负载是否开始；保持待办，由操作者查看材料并使用新批次编号重新安排必要的压测，不自动回填旧任务。

## 回滚

先完成或明确处理待办，停止原ocb/采集/负载，再执行 `bash update-node.sh --rollback`。恢复收尾0.1.0和报表0.1.1的原管理入口，不删除日志、报表、状态或备份。首次安装没有上一版时，用收尾安装器 `--detach` 撤回接入。

已完成0.2.0大文件任务的原始数据要保留；0.1.1不能重新生成超出其旧上限的报表。回滚不等于让旧版本获得新格式/容量能力。

不要对已接入节点直接运行原ocsync覆盖程序。若确需升级原OCRUN，先撤回/备份扩展并验证新版本兼容；0.2.0不宣称支持未知原程序版本。

## 验证限制

云端使用隔离Rocky 8.10/Python3.6.8、真实回环Redis/rsync、真实Excel依赖，以及合成监控与休眠程序。故障测试、跨版本升级和大日志测试在云端完成。真实sckocp授权、各型号硬件、全部压测工具、其他发行版与突然断电的存储持久性仍需指定环境验证；不以模拟结果代替。

## 0.2.4：包内 MLC 3.13 接入

配合工具套件 0.1.0-2、完整原生发行 0.2.1 使用。MLC 直接来自包内已校验的官方程序，不要求另外导入；程序、许可、文档进入哈希清单。旧导入材料保留，新包缺失或被修改时拒绝执行，不回退到旧副本。没有改变原版 sckocp、任务队列协议、单批启动、失败恢复或成功后关机规则。

新增 0.2.3 → 0.2.4 升级及原字节回滚检查。原生包同时检查 0.2.0 → 0.2.1 的解除配置后升级；配置中的节点仍拒绝覆盖。云端分别验证工具版本、依赖、少量内存的 MLC idle_latency、完整性拒绝与实际包管理操作，具体版本和运行记录见发行 RELEASE.json；完整 MLC 性能和硬件稳定性仍需指定节点验收。
