# OCRUN 旧系统增强套件 0.1.1

本版接口 0.3.2 和采集 0.12.12 从本次源码构建，独立 API 仍可单独安装；本地授权和原任务规则不变。更新及现场验收见同包 OPTIMIZATION.md。源码附件使用逐文件白名单，不再归档整个私有仓库；旧源码附件和 GitHub 自动生成的 Source code 不作为客户交付物。

统一命令为 `ocrun-plugin`。本套件面向旧 OCRUN 整套系统，分别提供控制端包和节点包；原 `ws`、`occt`、任务协议、资源服务器及结果目录保留。旧的 `mon-sensors-*` 命令作为兼容组件继续存在。

安装包不会启动压测、领取任务、安装新服务器服务或开监听端口。215、217、211、221 不自动升级。必须分别在明确选择的控制机和空闲测试节点安装；不自动分发给生产节点。

## 与完整系统的关系

|能力|旧系统增强套件|ocrun-next 完整系统|
|---|---|---|
|任务入口|新增 `ocrun-plugin tasks`，保留旧 `ws/occt`|新管理端|
|任务协议|原 Redis DB0 主机映射、每主机数据库|兼容协议及新部署认证配置|
|任务检查|名称、时长、重复项、批次冲突；节点执行前完整预检|相同执行引擎|
|执行生命周期|身份核对、时限、异常退出、子进程清理、明确启动单批次|相同执行引擎|
|工具库|绑定独立 RPM/DEB，原 bin 保留|节点包内集成|
|采集、自动收尾、恢复|复用采集 0.12.12、收尾 0.2.5|相同组件|
|机器报告|HTML/JSON 验收单及 Excel 明细|相同报告引擎|
|结果交付|原 rsync；下载副本核验 SHA-256；六文件回执|相同交付校验|
|查看结果|统一 results 入口，读取原共享目录|新管理端 results|
|服务器安装/数据库认证迁移|不更改旧服务器|完整系统提供新服务器部署|
|PXE、自动发现、网页仪表盘|本套件不提供|本次不增加这些功能|

相近的执行和验收能力不代表把旧服务器换成新服务器。原 occt 仍可直接写队列，其输入不经过新入口检查；增强节点会在执行前再次校验。未装增强组件的节点继续按原逻辑运行。

## 包与支持范围

- `ocrun-plugin-control-0.1.1`：旧控制端的任务、结果与验收入口。
- `ocrun-plugin-node-0.1.1`：旧节点的统一操作、接入安装、检查和回滚。
- `ocrun-workloads-0.1.0-3`：配套工具包，两者一起安装到节点。
- `sckocp-api-0.3.2.run`：仍可单独安装的本地接口；节点采集组件已携带同一接口代码，不必重复安装全局命令。

包位于 `/opt/ocrun-plugin/0.1.1`，入口 `/usr/bin/ocrun-plugin`，配置 `/etc/ocrun-plugin/config.json`。系统 Python 不替换；EL8 可使用原 `/usr/libexec/platform-python` 3.6.8。支持程度以本次 `VALIDATION.json` 为准，容器共用云宿主内核不代表真实硬件验证。

当前接入白名单：控制端为已审阅的 215 副本 MAIN 0.9.19 / DEV 0.9.20a，节点为原 0.9.24a 或已知采集/收尾组件组合。包可在不同发行版安装，不意味着任意旧 OCRUN 版本都可直接打补丁。未知文件、手工修改、属主/权限不符均保留并报错。

选用工具：stress 1.0.7、stress-ng 0.22.01、mprime 30.19b20（m1/m2/m4）、MLC 3.13、MBW 2.0、cyclictest 2.10、UnixBench 6.0.1。SPEC CPU2017 1.0.5 需从原授权归档导入；许可不转移。工具上游材料与许可沿用 v0.2.2 发布包。

## 控制端：215，root 安装，ocuser 使用

先核对 SHA256SUMS。以下是待现场验收操作；不要在另一台服务器照搬 IP 和主机编号。

```bash
dnf install ./ocrun-plugin-control-0.1.1-1.el8.x86_64.rpm
```

配置前只读核对原文件和结果目录。此例仅开放 K6C-165 的新任务入口，后续增加其他节点需明确修改配置；不自动扫描网络。

```bash
ocrun-plugin configure --role control --rdb-server 172.20.8.217 --allow-node K6C-165 --check
ocrun-plugin configure --role control --rdb-server 172.20.8.217 --allow-node K6C-165
```

进入原 `ws` 后，以 **ocuser** 使用：

```bash
ocrun-plugin check
ocrun-plugin status
ocrun-plugin catalog
ocrun-plugin tasks status --node K6C-165
ocrun-plugin tasks add --node K6C-165 --id PLUGIN-SYSTEM --task stress=60 --task stress-ng=60 --check
ocrun-plugin tasks add --node K6C-165 --id PLUGIN-SYSTEM --task stress=60 --task stress-ng=60
```

只有明确启用的节点才能用新入口提交任务。`--check` 不取任务、不入队。`strss` 会提示相近名称；原协议同名项目重复时必须使用相同时长。原控制端已有批次时拒绝覆盖：用原菜单处理旧批次，或按下述新入口取消/归档插件批次。

新批次未领取时取消，必须指定查询得到的 ID 与时间：

```bash
ocrun-plugin tasks cancel --node K6C-165 --id PLUGIN-SYSTEM --time 20260928-120000 --check
ocrun-plugin tasks cancel --node K6C-165 --id PLUGIN-SYSTEM --time 20260928-120000
```

已开始/失败批次不能直接取消数据库记录，应在节点 stop/recover。新入口不清空数据库，不删除未知键或共用映射，不覆盖旧菜单创建的批次；失败时保留队列。传输响应丢失时先 status，不能盲目重提。

## 节点：先在空闲 K6C-165，root

保留 sckocp 原版和授权，先确认已激活、已完成平台上报。原任务全部完成并停止原调度后再接入。包安装不自动停止它；不要用按名称批量杀进程。

K6C-165 已有收尾组件，但空闲调度可能仍在等待自动关机。工具包会拒绝此时安装。首次安装前，可用同包内的校验清单保护的便携入口停止空闲调度，不必手填 PID（有未完成任务或锁被占用时拒绝）：

```bash
tar -xzf ocrun-plugin-node-0.1.1-portable.tar.gz
/usr/libexec/platform-python -I -S -B ./ocrun-plugin-node-0.1.1/entry.py maintenance-stop
```

上述命令在 root 私有目录操作；其他发行版使用 `/usr/bin/python3`。便携入口只用于这次维护，不注册全局命令或写配置。尚未安装任何旧收尾组件的原版节点，应先按原运维流程确认空闲并停止调度，再执行安装。

EL 示例：

```bash
dnf install ./ocrun-workloads-0.1.0-3.el8.x86_64.rpm ./ocrun-plugin-node-0.1.1-1.el8.x86_64.rpm
```

Debian/Ubuntu 示例：

```bash
apt install ./ocrun-workloads_0.1.0-3_amd64.deb ./ocrun-plugin-node_0.1.1-1_amd64.deb
```

只添加缺少的系统依赖，不替换系统解释器。不安装完整 `ocrun-node` 到旧节点；两种安装形态互斥。

```bash
ocrun-plugin configure --role node --rdb-server 172.20.8.217 --log-server 172.20.8.211 --serial 260168795800086 --check
ocrun-plugin configure --role node --rdb-server 172.20.8.217 --log-server 172.20.8.211 --serial 260168795800086
ocrun-plugin attach --check
ocrun-plugin attach
ocrun-plugin check
ocrun-plugin tools list
```

编号使用主板序列号，应和旧日志命名一致。节点主机名自动读取，服务器必须和原 oc.env 一致。接入会依次安装独立 Excel 入口、采集组件、收尾组件、工具绑定；已有组件验证后升级，原 bin 与 oc.env 保留。记录位于 `/root/ocrun/.ocrun-plugin/attachment.json`，各组件自身备份也保留。

控制端下发任务后，只处理当前批次：

```bash
ocrun-plugin preflight
ocrun-plugin start
ocrun-plugin status
ocrun-plugin status --json
```

start 会先做完整节点预检，然后调用原调度。仍有原 60 秒初始化等待与原自动关机策略；不增加空闲持续领任务。没有任务时 start 拒绝启动。运行日志仍是 `/root/ocrun/.mon-sensors-finish/scheduler.log`。

## 恢复与结果

```bash
ocrun-plugin status --case CASE_ID
ocrun-plugin stop --case CASE_ID
ocrun-plugin retry --case CASE_ID
ocrun-plugin recover --case CASE_ID --interrupted
ocrun-plugin close-incomplete --case CASE_ID --reason '明确记录放弃收尾的原因'
```

retry 仅对已封存数据重试剩余收尾；recover 用于调度中断；不会重跑压测。任务失败、采集不可用、报告失败、上传未核验分别呈现，不把 complete 当作硬件合格。sckocp v1 传感器有效性与读数年龄继续标注未知，缺失值保持空白。

当前批次完成后，如需停止仍等待原关机策略的调度：

```bash
ocrun-plugin stop-scheduler
```

该命令只在没有未完成批次且锁空闲时，核对原调度身份并请求退出；不强制杀进程，不发起新任务。

控制端 ocuser 查看原 NFS 结果（文件名均相对结果根目录）：

```bash
ocrun-plugin results list --machine K6C-165_260168795800086
ocrun-plugin results show --receipt K6C-165_260168795800086/实际批次.finish.json
ocrun-plugin results verify --receipt K6C-165_260168795800086/实际批次.finish.json
ocrun-plugin results sample --mon K6C-165_260168795800086/实际批次.mon
```

HTML 路径在 show 中显示，可浏览器打开、打印或另存 PDF。results 是上传快照，不是实时网络采集。归档插件批次的队列记录前，要求 delivered 状态和本机六文件回执校验；结果文件永不删除：

```bash
ocrun-plugin tasks archive --node K6C-165 --id PLUGIN-SYSTEM --time 实际时间 --receipt K6C-165_260168795800086/实际批次.finish.json
```

## 接入失败、回滚和卸载

接入失败记录阶段，修复原因后 `ocrun-plugin attach --resume`。不会启动调度；中途断电造成未确认的文件变化时保留现场并报错，不猜测覆盖。

已接入节点回滚前，结束/恢复当前任务并停止原调度：

```bash
ocrun-plugin stop-scheduler
ocrun-plugin rollback --check
ocrun-plugin rollback
ocrun-plugin unconfigure
```

恢复接入前的入口、模块、属主、权限和工具绑定；采样、报告、状态、备份、已安装 sckocp 均保留，旧调度不会自动启动。回滚遇到接入后手工修改的文件拒绝覆盖。回滚途中中断，可重复 rollback；若无法核对状态，按错误位置保留现场处理。

控制端只需 root 执行 `ocrun-plugin unconfigure`，原程序从未修改。然后 `dnf remove ocrun-plugin-control` 或 `apt remove ocrun-plugin-control`；节点使用对应 node 包名。包管理器拒绝删除仍有配置的插件，避免旧 hook 指向已删除代码。工具包独立保留，未解除其他绑定时不要删除。

## 验证说明

从套件 0.1.0 更新到 0.1.1：先按上节显式 rollback / unconfigure，再移除旧套件包，安装新包并重新 configure / attach。保留工具包、原始数据、接入备份和旧安装包。不要直接覆盖仍接入的套件，也不要使用强制包管理参数。撤回新版本采用相同解除流程后重装旧包；旧控制机系统和 ws/occt 不变。

已有 v0.2.2 的工具与报告来源固定 SHA-256，组件版本可追溯；本次云端测试另外验证新控制入口、旧 Redis 共用数据库、重复任务、无效任务不改队列、接入/回滚、安装状态以及通过旧执行器生成报告并回读核验。具体通过项目与发行版写入 VALIDATION.json，不能用历史证据代替本次验证。

真实 215/K6C-165 验收尚须由用户执行。建议首次仅 PLUGIN-SYSTEM 的 stress→stress-ng 各 60 秒，检查两步 duration_reached、cleanup_confirmed、报告路径、回执及共享目录 verify，再考虑扩展允许节点名单。
# BITS-o 迁移入口

当前发行的旧系统增强包已改名为 `bits-o-control`、`bits-o-node`，工具包为 `bits-o-workloads`，入口 `bits-o`。本文件下面保留原 0.1.1 操作记录，**当前版本安装请使用 [BITS 简明部署](../deployment/BITS.md)**，迁移与回滚见 [0.2.4](../releases/0.2.4.md)。原 `occt` 仍可下发任务，节点自动完成压测、采集、报告与交付。
