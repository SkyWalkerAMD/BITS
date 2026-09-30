# OCRUN 0.2.3 原生发行包

本版接口为 0.3.2、采集为 0.12.12。只读 `ocrun-center results` 允许有结果文件读取权限的普通账户使用；配置、发布和回滚仍需要 root。更新说明、旧版升级及现场验收见同包 OPTIMIZATION.md。

客户源码附件使用逐文件白名单，不再整仓库归档。请分发本版上传的安装包/发行归档，不要分发旧源码附件或 GitHub 自动生成的 Source code；详见 OPTIMIZATION.md 的交付范围说明。

本版增加任务结束后自动生成的内部验收 HTML 报告，保留 Excel；旧版节点和215可用独立插件接入。报告字段、六份交付文件、升级/恢复及现场验收见 [机器压测报告单](../reports/ACCEPTANCE-REPORT.md)。

适用于新部署的 x86-64 管理服务器和明确选定的测试节点。完整发行包括管理端 `ocrun-center`、节点端 `ocrun-node` 两个角色，各提供 RPM 与 DEB；节点包内包含所选压测工具，不需要再安装 `ocrun-workloads`。管理端包内同时包含两种节点包，用于内网分发。

现有 215/217/211/221、原 0730/0.9.24a 节点继续使用原部署及独立兼容组件；本包不会自动迁移它们。没有 PXE 组件。不安装、修改或激活 sckocp 本体。

## 获取与校验

在仓库 Actions 中选择 **OCRUN integrated distribution**。成功运行最后的 `ocrun-system-0.2.3-运行号` artifact 含发行归档、SHA256SUMS、RELEASE.json 和分系统验证材料。

```bash
tar -xzf ocrun-system-0.2.3.tar.gz
cd ocrun-system-0.2.3
sha256sum -c SHA256SUMS
```

SHA-256 从受信任的私有仓库运行获取，不等同于 RPM/GPG 签名；目前没有配置包签名密钥。`RELEASE.json` 关联源码提交、云端运行、安装包与验证结果。只下载自己需要的格式：

| 角色 | EL 8–10 | Debian 11–13 / Ubuntu 22.04、24.04、26.04 |
| --- | --- | --- |
| 管理端 | `ocrun-center-0.2.3-1.el8.x86_64.rpm` | `ocrun-center_0.2.3-1_amd64.deb` |
| 节点端（含工具） | `ocrun-node-0.2.3-1.el8.x86_64.rpm` | `ocrun-node_0.2.3-1_amd64.deb` |

RPM 使用 EL8 构建的工具基线；后缀 `.el8` 不代表只面向 EL8。Rocky/AlmaLinux 8/9/10、Debian 11/12/13、Ubuntu 三个 LTS 版本分别运行安装验收，以该次发行清单为准。RHEL 需要有效软件源订阅和另外验收；不泛化为所有 EL 衍生发行版。EL10 主机自身必须满足其发行版 CPU 要求。

云端真实验证包管理、systemd、认证数据库、资源服务、任务执行及文件交付。监控数据使用明确标注的模拟来源，容器共享云端宿主内核；sckocp 激活、传感器、P95 指令集与长时间稳定性、cyclictest 实时延迟仍需指定硬件验证。`--check` 不是硬件验收。

## 1. 新管理服务器，root

使用当前目录里的本地安装包，依赖由发行版包管理器解析。不要同时安装两种格式。

```bash
# Rocky / AlmaLinux
dnf install ./ocrun-center-0.2.3-1.el8.x86_64.rpm
# Debian / Ubuntu
apt install ./ocrun-center_0.2.3-1_amd64.deb
```

安装包本身不配置或启动服务。将下面示例 IP/网段换成**新管理服务器**实际内网地址和授权节点网段；不要使用既有生产服务器地址：

```bash
ocrun-center check
ocrun-center setup --address 192.168.50.10 --network 192.168.50.0/24 --check
ocrun-center setup --address 192.168.50.10 --network 192.168.50.0/24 --apply
ocrun-center check
ocrun-center publish-node
```

首次 `--check` 若提示缺少 nginx、数据库等系统依赖，这是未配置服务器的预检结果；它不会安装依赖。核对地址与网段后，`--apply` 会安装所需原生系统依赖，创建管理账户、启用 OCRUN 自有服务，输出节点安装包 URL 和 SHA-256。访问范围显式限定；Redis/Valkey 保留保护模式和认证。不会关闭防火墙、SELinux 或暴露免认证任务库。存在冲突服务/未知配置时保留现场并拒绝覆盖。软件包安装和服务器配置是两个可审查的步骤。

管理端本次在同一新服务器提供任务库、程序资源和结果存储，结果为 `/data/cds/result`。不自动挂载或修改原有 221 的 NFS，也不推定旧环境存储映射。

导出单个节点的私有连接材料：

```bash
ocrun-server node-config --node TEST-NODE --output /root/TEST-NODE.json
```

该文件须通过可信 SSH/SFTP 等方式送到目标节点 `/root/TEST-NODE.json`，保持 root 属主和 0600。它含数据库凭据，不能放入公共下载目录、Git 或测试日志。HTTP 安装包地址只用于内网程序分发，节点下载后必须使用从可信管理终端获得的 SHA-256 核验。

## 2. 新测试节点，root

先安装并激活原版 sckocp，完成它要求的平台报告；本发行不改变其许可和离线授权。原 1.2.0 支持 v1 数据格式；有效性和读数年龄仍未知。

```bash
# Rocky / AlmaLinux
dnf install ./ocrun-node-0.2.3-1.el8.x86_64.rpm
# Debian / Ubuntu
apt install ./ocrun-node_0.2.3-1_amd64.deb
```

节点包与独立 `ocrun-workloads` 包互斥，因为已经包含同一工具清单。工具程序位于 `/opt/ocrun-workloads/0.1.0`，不会替换系统 `/usr/bin/stress` 等程序。安装 Python 依赖使用系统包管理器，不替换系统 Python。采集与收尾支持 Python 3.6+，原生报表只需包内纯 Python XLSX 库，避免跨 Python 版本共享 NumPy 二进制。

```bash
ocrun-node check
ocrun-workloads list
ocrun-node configure --config /root/TEST-NODE.json --serial YOUR-SERIAL --check
ocrun-node configure --config /root/TEST-NODE.json --serial YOUR-SERIAL
ocrun-node check
```

节点实际 `hostname` 必须是连接文件中的 `TEST-NODE`；`--serial` 填稳定的硬件/资产编号，不要每次变化。默认保留成功交付后 30 分钟空闲自动关机、登录用户或压测活动重置倒计时的规则。需要保持开机的验收节点，在**首次配置的两条命令**都加 `--keep-on`。这项选择明确记录，重复配置不得悄悄改变策略。

配置中断后用同一份连接文件、编号和参数重新执行，会核验已准备的文件并继续；不同输入或被改动的文件会被保留并拒绝覆盖。配置完成后不会自动启动调度或压测。

独立 `sckocp-api-0.3.2.run` 仍单独交付安装。节点包内使用同版接口模块，并不要求另起网络服务，也不会修改本机 sckocp。

## 3. 任务与结果

管理服务器的 `ocuser` 账户使用 `ws`/`occt` 菜单，或明确命令：

```bash
ocrun-server task add --node TEST-NODE --id FIRST-CHECK --task stress=60 --task stress-ng=60
ocrun-server task status --node TEST-NODE
```

测试节点 root：

```bash
ocrun-node preflight
ocrun-node start
ocrun-node status --human
```

预检不取走队列。一次 `start` 等待 60 秒初始化，再执行当时已核验的批次；批次完成后不循环领取新任务。任务拼写错误、缺少程序、授权采集失败或报表环境不可用会明确失败。P95 仅 m1/m2/m4，支持 `p95-no_m1`、`p95-avx_m2`、`p95-fma3_m4` 等；所需 CPU 指令需满足。保留原任务协议，同名重复项目必须使用同一时长。

结果保存在节点 `/var/log/ocrun-node`，交付至管理端 `/data/cds/result/主机名_编号/`。正常生成 `.mon`、`.mon.sckocp.jsonl`、`.xlsx`、`.report.html`、`.report.json`、`.finish.json`，上传后下载副本核对 SHA-256。压测执行结果、未知传感器质量与文件交付结果分别记录，`complete` 不代表硬件合格。VRM/PSU/风扇等原来源缺失列保持空白。

运行记录：`/var/lib/ocrun-node/app/.mon-sensors-finish/scheduler.log`。精确批次 JSON：

```bash
ocrun-node status --case CASE_ID
ocrun-node stop --case CASE_ID
ocrun-node retry --case CASE_ID
ocrun-node recover --case CASE_ID --interrupted
```

`stop` 核对本次 PID/启动时间后请求停止当前批次；`retry` 处理已封存的失败阶段；`recover --interrupted` 处理异常中断，均不会自动重跑压测。不可恢复的批次可显式 `ocrun-node close-incomplete --case CASE_ID --reason '具体原因'`，保存失败证据，不伪造完成回执。有失败待处理时不进入自动关机流程。

长日志沿用受限流式校验与报表，每张明细表 25 万行，最多 400 万行/16 GiB；预检估算采集、临时副本及报表空间。超预算须显式拆批，保留完整行与来源哈希，不静默删旧数据。小日志也使用流式报表，布局可能与旧 pandas 报表不同，原始 `.mon` 协议不变。

## 4. 工具版本与受许可材料

节点包直接包含 stress 1.0.7、stress-ng 0.22.01、mprime 30.19b20、MLC 3.13、MBW 2.0、rt-tests/cyclictest 2.10、UnixBench 6.0.1。一个 mprime 程序通过独立任务配置区分 P95 模式，移除新任务目录中的 sysjitter 和未选择的工具，保留原包文件不动。

MLC 官方程序直接安装在 `/opt/ocrun-workloads/0.1.0/mlc/mlc`，原许可和 redist.txt 在 `licenses/mlc/`，使用文档在 `mlc/`。`ocrun-workloads list` 应显示 `mlc: 3.13, delivery: included`，无需 `import-mlc`。程序及许可文件受完整性清单保护，缺失或修改会在任务取出前报错；使用与后续分发遵循 Intel 原许可。

部分硬件上的 MLC 要求预留大页内存；包安装不会修改大页或内核参数。缺少条件时原程序会报告原因并记录步骤失败，准备方法与云端验证边界见 WORKLOADS-MANUAL.md。云端仅临时预留大页完成短时测试，不能据此推断客户节点已满足全套性能测试条件。

SPEC CPU2017 1.0.5 仍从用户持有的原包导入，此操作不会运行压测：

```bash
ocrun-workloads import-spec --archive /root/0730.tar.gz --check
ocrun-workloads import-spec --archive /root/0730.tar.gz
```

必须是 `SOURCES.json` 中固定 SHA-256 对应的原始归档，不能仅按文件名判断。SPEC 从原 0730 中提取原始文件，不升级到其他版本。未导入时 SPEC 任务预检拒绝执行。完整清单及上游链接见 `WORKLOADS-MANUAL.md`/仓库 `workload_suite/MANUAL.md`。

## 5. 更新、回滚与卸载

0.2.3 可用于新部署，也可在显式解除配置后更新已安装的 0.2.2。已配置节点拒绝直接覆盖/卸载；未完成任务先处理状态；程序被手工改动或新版本目录含未知文件时拒绝覆盖，不使用强制包管理参数绕过。新包安装不自动恢复旧连接或重新启动压测。

节点确认无运行批次并完成收尾后：

```bash
ocrun-node stop-scheduler
ocrun-node detach --check
ocrun-node detach
```

`detach` 把配置、私有连接材料和应用状态转存到 `/var/lib/ocrun-node/detached-*`，日志和导入工具保留。`/etc` 与 `/var` 分区可以不同；先验证备份再移出应用，意外中断后用同一条 `detach` 恢复。之后用本手册安装命令将 0.2.2 更新为 0.2.3；按新部署章节显式重新配置。历史记录在返回的备份目录保留，不能将旧状态文件直接混入新批次。

若需回到 0.2.2，先完成收尾并 detach，然后卸载 0.2.3，安装从原 v0.2.2 Release 保留且核验过的旧包，再显式重新配置。不使用强制降级参数；原 v0.2.2 发布文件保持不变。独立工具套件升级/重新绑定详见 WORKLOADS-MANUAL.md。

管理端先明确停止部署：

```bash
ocrun-center rollback
```

这会停止本次部署的中心服务并还原其受管配置；先确保目标节点不再依赖它。数据库与结果数据保留，不删除已有任务证据。再按平台卸载：

```bash
# 仅在对应机器卸载对应角色
dnf remove ocrun-node
dnf remove ocrun-center
# 或 Debian / Ubuntu
apt remove ocrun-node
apt remove ocrun-center
```

单独工具包、原 OCRUN 插件/收尾兼容升级参见各自手册，不能与原生新节点配置流程混用。

## 最小现场验收

选定一台新管理服务器和一台空闲节点，均由 root 安装配置；管理任务可切换为 ocuser。先核对发行校验、`check` 与 `preflight`，下发 stress → stress-ng 各 60 秒；节点仅执行一次 `start`。预期：两项时长受控、进程清理确认、六份结果可由管理账户读取、回执列出的五个结果文件哈希一致。再显式安排其他工具和硬件验证，不自动扩展到生产节点。
