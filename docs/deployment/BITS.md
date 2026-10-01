# BITS 部署与旧系统接入

|用途|安装包|常用入口|
|---|---|---|
|新管理服务器|bits-center|bits-center|
|新测试节点，内含工具|bits-node|bits-node|
|旧 215 控制机增强|bits-o-control|bits-o|
|旧 OCRUN 测试节点增强|bits-o-node + bits-o-workloads|bits-o|
|第三方独立本地接口|sckocp-api|sckocp-api|

**完整 BITS：管理端安装 `bits-center`，节点安装 `bits-node`，节点另备已激活的原版 `sckocp`。** `bits-node` 已集成选定压测工具、本地接口、批次采集和报表收尾功能，无需另装 `bits-o-workloads`、独立 `sckocp-api .run` 或历史 `mon-sensors-*.run`。

`bits-o-workloads` 是旧系统增强包 `bits-o-node` 的独立依赖。它也作为独立附件出现在完整系统 Release 中，但不是完整 BITS 的安装依赖；`bits-node` 与它包含相同工具文件，包管理器会阻止两者混装。完整系统的工具随 `bits-node` 提供，安装包名称保持 `bits-` 前缀。

RPM 面向已验收的 EL 8–10，DEB 面向 Debian 11–13 / Ubuntu 22.04、24.04、26.04，均为 x86-64。安装前校验 Release SHA256SUMS。系统仓库仍需提供包声明的依赖；包内包含选定工具，不等于包含整套操作系统依赖离线源。

节点需要单独安装、激活并完成平台登记的原版 sckocp。BITS 不代办激活，也不分发授权码。硬件状态直接使用 `sckocp`、`sckocp info`；采集仅在任务批次内自动运行。

## 旧环境：只处理 215 和明确选择的节点

现有 217 Redis、211 分发/日志入口、221 NFS 保持现有配置。无需到 211 安装本套件；215 的结果查看增强是可选的，原 occt 本来就能继续下发任务。

以下为首次安装的 EL 示例，账户 root。**有任务、采集或旧调度仍运行时安装会拒绝，不会代停生产任务。** 已安装旧 ocrun-plugin 的机器先按 [迁移与回滚](../releases/0.2.4.md) 处理，不直接覆盖。

215：
```bash
dnf install ./bits-o-control-0.2.0-1.el8.x86_64.rpm
bits-o setup --allow-node K6C-144 --check
bits-o setup --allow-node K6C-144
```

节点 K6C-144：
```bash
dnf install ./bits-o-workloads-0.1.0-4.el8.x86_64.rpm ./bits-o-node-0.2.0-1.el8.x86_64.rpm
bits-o setup --check
bits-o setup
```

自动读取原 `/root/ocrun/oc.env` 的主服务器和 sysfs 主板序列号。序列号缺失或为占位值时明确指定 `--serial`；不会猜测机器身份。配置和接入可重复执行，未知文件差异会阻止覆盖。

215 的 ocuser 继续 `ws`、`occt`，按原菜单添加任务。节点在已经开机且完成安装时：
```bash
bits-o preflight
bits-o start
bits-o status
```

无需为了启动新批次重启。原开机入口仍可经已接入的 ocb 执行当前批次。任务结束会停止采集、封存、生成 Excel/HTML/JSON、上传并下载验证哈希，随后沿用原自动关机策略；不持续领取后来新加的任务。前一轮残留的空闲调度可在确认完成后使用 `bits-o stop-scheduler` 停止，再明确启动新批次。

旧 occt 可以输入已移除的旧工具名或拼写错误；节点会在领取之前给出错误并保留队列，由原菜单修正。使用 `bits-o catalog` 查看支持名称。相同工具重复出现时，受原协议限制应使用相同运行时长。

215 ocuser 查看结果：
```bash
bits-o tasks status --node K6C-144
bits-o results list
```
详细验证使用 `bits-o results verify --receipt 相对路径 --receipt-sha256 节点给出的哈希 --json`。浏览器打开核验后的 `.report.html`，可打印或另存 PDF。原菜单创建的任务仍在原菜单管理；新插件不会自动认领并删除这些任务。

## 新完整系统

从 **BITS 0.2.5** 起可自动识别已有网络，root 执行：

```bash
bits-center network
bits-center setup --auto --check
bits-center setup --auto --apply
bits-center check
```

`network` 只读列出地址，也支持 `--json`。`setup --auto` 从处于启用状态的内网 IPv4 中选择服务地址，依据该地址的前缀计算允许访问的网段，展示计划后复用原安装器配置 TCP 80/873/6379、数据库认证及服务。它沿用网卡 IP、路由、网关和 DNS，不增加 DHCP、地址分配或 PXE。

多张可用网卡时不猜测，预检和应用两条命令都加 `--interface ens192`（换成实际名称）。同一网卡有多个地址时再加 `--address IP`。回环、链路本地、公网地址及常见容器/隧道接口不参与自动选择。DHCP 地址会提示保留租约或另行配置固定地址；程序不会擅自把它改成静态地址。

允许网段可以用 `--network CIDR` 显式缩小；它必须是包含服务地址的私有 IPv4 网段。自动推导小于 /16 的大网段会要求明确指定范围。多网段、路由隔离和外部防火墙仍按实际网络规划；自动检测不代表节点端到端连通已经验证。网络检测存在歧义或检测期间变化时，不开始应用配置。重复配置继续遵守已有地址、网段与文件完整性检查。

原显式配置方式继续可用（0.2.4 也使用此方式）：

安装 `bits-center` 后由管理员明确给出服务地址及允许网段：
```bash
bits-center setup --address 内网IP --network 允许CIDR --check
bits-center setup --address 内网IP --network 允许CIDR --apply
bits-center check
bits-center menu
```
上方 IP/CIDR 为占位符，须替换。新数据库启用认证，`bits-center node-config --node 节点名 --output /root/node.json` 导出私有连接文件，按受控方式交给对应节点；不要放入公开下载目录。

节点安装 `bits-node` 后：
```bash
bits-node setup --config /root/node.json --check
bits-node setup --config /root/node.json
bits-node preflight
bits-node start
bits-node status --human
```
新完整节点不要求 `/root/ocrun` 原程序；检测到该目录的旧节点应使用 BITS-o。新系统更详细网络、依赖和卸载说明仍见 [完整系统手册](DISTRIBUTION.md)。

## 故障恢复与接口

`bits-o status --json` 或 `bits-node status` 保留机器可读输出。报表/交付失败用 `retry --case 批次编号`；调度中断按记录使用 `recover --case 批次编号 --interrupted`。两者不重跑压测。工作进程残留、原始文件变化、回执不符时先解决明确错误，不能把状态手工改为完成。

独立接口安装后：
```bash
sckocp-api --details --timeout 20
```
时序固定只输出 Primary 组，缺失项显示未提供；没有 rmal 入口。独立 API 与内置采集模块从同一源码版本构建，但独立 API 无需安装 BITS。安装与卸载详见 [API 手册](../monitoring/SCKOCP-API-操作手册.md)。
