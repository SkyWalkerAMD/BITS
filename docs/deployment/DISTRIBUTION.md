当前品牌为 BITS。首次部署与旧系统区别见 [简明部署手册](BITS.md)，旧包迁移和回滚见 [0.3.0 说明](../releases/0.3.0.md)。下述路径中的 ocrun 为保留的兼容目录名。

# BITS 0.3.0 原生发行包

本版接口为 0.4.0、内部采集为 0.13.0。只读 `bits-center results` 允许有结果文件读取权限的普通账户使用；配置、发布和回滚仍需要 root。更新说明、旧版升级及现场验收见同包 OPTIMIZATION.md。

客户源码附件使用逐文件白名单；安装使用 Release 的 RPM/DEB。保留在私有归档中的旧研究源码不属于公开交付内容。

本版增加任务结束后自动生成的内部验收 HTML 报告，保留 Excel；旧版节点和215可用独立插件接入。报告字段、六份交付文件、升级/恢复及现场验收见 [机器压测报告单](../reports/ACCEPTANCE-REPORT.md)。

适用于新部署的 x86-64 管理服务器和明确选定的测试节点。完整发行包括管理端 `bits-center`、节点端 `bits-node` 两个角色，各提供 RPM 与 DEB；节点包内包含所选压测工具，不需要再安装 `bits-o-workloads`。管理端包内同时包含两种节点包，用于内网分发。

现有 215/217/211/221、原 0730/0.9.24a 节点继续使用原部署及独立兼容组件；本包不会自动迁移它们。没有 PXE 组件。不安装、修改或激活 sckocp 本体。

## 获取与校验

从 BITS 的 v0.3.0 Release 下载对应 RPM/DEB 与 SHA256SUMS。完整归档另含 RELEASE.json 和分系统验证材料；私有云端构建使用 **BITS integrated distribution**，成功产物为 `bits-system-0.3.0-运行号`。

```bash
tar -xzf bits-system-0.3.0.tar.gz
cd bits-system-0.3.0
sha256sum -c SHA256SUMS
```

SHA-256 从受信任的私有仓库运行获取，不等同于 RPM/GPG 签名；目前没有配置包签名密钥。`RELEASE.json` 关联源码提交、云端运行、安装包与验证结果。只下载自己需要的格式：

| 角色 | EL 8–10 | Debian 11–13 / Ubuntu 22.04、24.04、26.04 |
| --- | --- | --- |
| 管理端 | `bits-center-0.3.0-1.el8.x86_64.rpm` | `bits-center_0.3.0-1_amd64.deb` |
| 节点端（含工具） | `bits-node-0.3.0-1.el8.x86_64.rpm` | `bits-node_0.3.0-1_amd64.deb` |

RPM 使用 EL8 构建的工具基线；后缀 `.el8` 不代表只面向 EL8。Rocky/AlmaLinux 8/9/10、Debian 11/12/13、Ubuntu 三个 LTS 版本分别运行安装验收，以该次发行清单为准。RHEL 需要有效软件源订阅和另外验收；不泛化为所有 EL 衍生发行版。EL10 主机自身必须满足其发行版 CPU 要求。

云端真实验证包管理、systemd、认证数据库、资源服务、任务执行及文件交付。监控数据使用明确标注的模拟来源，容器共享云端宿主内核；sckocp 激活、传感器、P95 指令集与长时间稳定性、cyclictest 实时延迟仍需指定硬件验证。`--check` 不是硬件验收。

## 1. 新管理服务器，root

使用当前目录里的本地安装包，依赖由发行版包管理器解析。不要同时安装两种格式。

```bash
# Rocky / AlmaLinux
dnf install ./bits-center-0.3.0-1.el8.x86_64.rpm
# Debian / Ubuntu
apt install ./bits-center_0.3.0-1_amd64.deb
```

安装包本身不配置或启动服务。将下面示例 IP/网段换成**新管理服务器**实际内网地址和授权节点网段；不要使用既有生产服务器地址：

```bash
bits-center check
bits-center setup --address 192.168.50.10 --network 192.168.50.0/24 --check
bits-center setup --address 192.168.50.10 --network 192.168.50.0/24 --apply
bits-center check
bits-center publish-node
```

首次 `--check` 若提示缺少 nginx、数据库等系统依赖，这是未配置服务器的预检结果；它不会安装依赖。核对地址与网段后，`--apply` 会安装所需原生系统依赖，创建管理账户、启用 BITS 专用服务，输出节点安装包 URL 和 SHA-256。访问范围显式限定；Redis/Valkey 保留保护模式和认证。不会关闭防火墙、SELinux 或暴露免认证任务库。存在冲突服务/未知配置时保留现场并拒绝覆盖。软件包安装和服务器配置是两个可审查的步骤。

管理端本次在同一新服务器提供任务库、程序资源和结果存储，结果为 `/data/cds/result`。不自动挂载或修改原有 221 的 NFS，也不推定旧环境存储映射。

导出单个节点的私有连接材料：

```bash
bits-center node-config --node TEST-NODE --output /root/TEST-NODE.json
```

该文件须通过可信 SSH/SFTP 等方式送到目标节点 `/root/TEST-NODE.json`，保持 root 属主和 0600。它含数据库凭据，不能放入公共下载目录、Git 或测试日志。HTTP 安装包地址只用于内网程序分发，节点下载后必须使用从可信管理终端获得的 SHA-256 核验。

## 2. 新测试节点，root

先安装并激活原版 sckocp，完成它要求的平台报告；本发行不改变其许可和离线授权。原 1.2.0 支持 v1 数据格式；有效性和读数年龄仍未知。

```bash
# Rocky / AlmaLinux
dnf install ./bits-node-0.3.0-1.el8.x86_64.rpm
# Debian / Ubuntu
apt install ./bits-node_0.3.0-1_amd64.deb
```

节点包与独立 `bits-o-workloads` 包互斥，因为已经包含同一工具清单。工具程序位于 `/opt/bits/workloads/0.1.0`，不会替换系统 `/usr/bin/stress` 等程序。安装 Python 依赖使用系统包管理器，不替换系统 Python。采集与收尾支持 Python 3.6+，原生报表只需包内纯 Python XLSX 库，避免跨 Python 版本共享 NumPy 二进制。

```bash
bits-node check
bits-node tools list
bits-node configure --config /root/TEST-NODE.json --serial YOUR-SERIAL --check
bits-node configure --config /root/TEST-NODE.json --serial YOUR-SERIAL
bits-node check
```

节点实际 `hostname` 必须是连接文件中的 `TEST-NODE`；`--serial` 填稳定的硬件/资产编号，不要每次变化。默认保留成功交付后 30 分钟空闲自动关机、登录用户或压测活动重置倒计时的规则。需要保持开机的验收节点，在**首次配置的两条命令**都加 `--keep-on`。这项选择明确记录，重复配置不得悄悄改变策略。

配置中断后用同一份连接文件、编号和参数重新执行，会核验已准备的文件并继续；不同输入或被改动的文件会被保留并拒绝覆盖。配置完成后不会自动启动调度或压测。

独立 `sckocp-api-0.4.0.run` 仍单独交付安装。节点包内使用同版接口模块，并不要求另起网络服务，也不会修改本机 sckocp。

## 3. 任务与结果

管理服务器的 `bits` 账户使用 `bits-center menu` 菜单，或明确命令：

```bash
bits-center task add --node TEST-NODE --id FIRST-CHECK --task stress=60 --task stress-ng=60
bits-center task status --node TEST-NODE
```

测试节点 root：

```bash
bits-node preflight
bits-node start
bits-node status --human
```

预检不取走队列。一次 `start` 等待 60 秒初始化，再执行当时已核验的批次；批次完成后不循环领取新任务。任务拼写错误、缺少程序、授权采集失败或报表环境不可用会明确失败。P95 仅 m1/m2/m4，支持 `p95-no_m1`、`p95-avx_m2`、`p95-fma3_m4` 等；所需 CPU 指令需满足。保留原任务协议，同名重复项目必须使用同一时长。

结果保存在节点 `/var/log/bits/node`，交付至管理端 `/data/cds/result/主机名_编号/`。正常生成 `.mon`、`.mon.sckocp.jsonl`、`.xlsx`、`.report.html`、`.report.json`、`.finish.json`，上传后下载副本核对 SHA-256。压测执行结果、未知传感器质量与文件交付结果分别记录，`complete` 不代表硬件合格。VRM/PSU/风扇等原来源缺失列保持空白。

运行记录：`/var/lib/bits/node/app/state/scheduler.log`。精确批次 JSON：

```bash
bits-node status --case CASE_ID
bits-node stop --case CASE_ID
bits-node retry --case CASE_ID
bits-node recover --case CASE_ID --interrupted
```

`stop` 核对本次 PID/启动时间后请求停止当前批次；`retry` 处理已封存的失败阶段；`recover --interrupted` 处理异常中断，均不会自动重跑压测。不可恢复的批次可显式 `bits-node close-incomplete --case CASE_ID --reason '具体原因'`，保存失败证据，不伪造完成回执。有失败待处理时不进入自动关机流程。

长日志沿用受限流式校验与报表，每张明细表 25 万行，最多 400 万行/16 GiB；预检估算采集、临时副本及报表空间。超预算须显式拆批，保留完整行与来源哈希，不静默删旧数据。小日志也使用流式报表，布局可能与旧 pandas 报表不同，原始 `.mon` 协议不变。

## 4. 工具版本与受许可材料

节点包直接包含 stress 1.0.7、stress-ng 0.22.01、mprime 30.19b20、MLC 3.13、MBW 2.0、rt-tests/cyclictest 2.10、UnixBench 6.0.1。一个 mprime 程序通过独立任务配置区分 P95 模式，移除新任务目录中的 sysjitter 和未选择的工具，保留原包文件不动。

MLC 官方程序直接安装在 `/opt/bits/workloads/0.1.0/mlc/mlc`，原许可和 redist.txt 在 `licenses/mlc/`，使用文档在 `mlc/`。`bits-node tools list` 应显示 `mlc: 3.13, delivery: included`，无需 `import-mlc`。程序及许可文件受完整性清单保护，缺失或修改会在任务取出前报错；使用与后续分发遵循 Intel 原许可。

部分硬件上的 MLC 要求预留大页内存；包安装不会修改大页或内核参数。缺少条件时原程序会报告原因并记录步骤失败，准备方法与云端验证边界见 WORKLOADS-MANUAL.md。云端仅临时预留大页完成短时测试，不能据此推断客户节点已满足全套性能测试条件。

SPEC CPU2017 1.0.5 仍从用户持有的原包导入，此操作不会运行压测：

```bash
bits-node tools import-spec --archive /root/0730.tar.gz --check
bits-node tools import-spec --archive /root/0730.tar.gz
```

必须是 `SOURCES.json` 中固定 SHA-256 对应的原始归档，不能仅按文件名判断。SPEC 从原 0730 中提取原始文件，不升级到其他版本。未导入时 SPEC 任务预检拒绝执行。完整清单及上游链接见 `WORKLOADS-MANUAL.md`/仓库 `docs/workloads/MANUAL.md`。

## 5. 更新、回滚与卸载

0.3.0 改用独立 BITS 目录，不对旧配置进行包管理器自动迁移。已有批次必须先完成或明确处置，程序有未知修改时拒绝覆盖。完整步骤见 [0.3 目录迁移与回滚](MIGRATION-0.3.md)。

在旧节点执行 `bits-node stop-scheduler`、`bits-node detach --check`、`bits-node detach`，保存输出的备份目录，再移除旧节点包、安装 0.3.0。0.2.3–0.2.5 的旧备份在 `/var/lib/ocrun-node/detached-*`；0.3.0 自身的备份在 `/var/lib/bits/node/detached-*`。

新包使用 `bits-node migrate --from 旧备份绝对目录 --check` 预检，再去掉 `--check` 导入。它恢复同一主机的连接和已终结批次索引，不启动任务、不移动或改写原监控、报告及完成回执。未完成批次、修改过的原程序、结果哈希变化或批次 ID 冲突都需先处理。旧记录仍指向原日志，不能删除 `/var/log/ocrun-node`。

新中心目录也有变化。服务端旧数据库和共享结果不能用节点迁移命令搬迁；当前中心安装器拒绝覆盖旧部署。管理端数据迁移必须在维护窗口另行确认存储、权限、私有连接与回退方案；旧 215/211/217/221 不在本次部署范围。

卸载配置过的 0.3.0 节点前显式执行 `bits-node detach`；中心在节点均停止依赖它后执行 `bits-center rollback`。然后按对应角色使用 `dnf remove bits-node` / `dnf remove bits-center` 或 `apt remove`。日志、数据库、备份和已封存报告保留；不使用强制覆盖或强制降级。

## 最小现场验收

选定一台新管理服务器和一台空闲节点，均由 root 安装配置；管理任务可切换为 bits。先核对发行校验、`check` 与 `preflight`，下发 stress → stress-ng 各 60 秒；节点仅执行一次 `start`。预期：两项时长受控、进程清理确认、六份结果可由管理账户读取、回执列出的五个结果文件哈希一致。再显式安排其他工具和硬件验证，不自动扩展到生产节点。
