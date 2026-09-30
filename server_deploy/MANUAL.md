# OCRUN 跨发行版中心 0.1.3 操作手册

## 范围与已验证系统的关系

本包用于**另建一台中心服务器**，合并任务数据库、任务管理、HTTP 资源地址、rsync 日志入口和本机结果存储。使用原 OCRUN 的主机数据库映射、`IDS/DATES/TASKS` 和 Lua 协议。新服务器要求数据库认证并保持保护模式开启；连接它的节点使用收尾组件 0.2.4 和私密连接文件。

它不会升级现有 215/217/211/221 服务器，也不会连接、改动其他生产节点。此前根目录的 `install-server.sh` 属于另一套新键空间协议；不要与本包混装。

目标为 x86_64 的 Rocky/AlmaLinux 8、9、10，Ubuntu 22.04/24.04/26.04 LTS，Debian 11/12/13。RHEL 8–10 仅有识别和包映射，需有效订阅，未做 RHEL 实机验收。其他 EL 衍生版不因 `ID_LIKE=rhel` 自动视为支持。实际验证级别以随包测试记录为准；容器测试使用发行版用户空间和真实 systemd 服务，但共享云端宿主内核。

**中心操作系统支持不等于所有设备端已验证。** 设备端硬件基线仍是 Rocky 8.10 / Python 3.6.8 / 原 OCRUN 0.9.24a / sckocp 1.2.0 / API 0.3.1 / 插件 0.12.9 / 报表 0.2.0。收尾 0.2.1 已有现场记录，0.2.2 增加新中心认证，本轮 0.2.4 接入新版工具；新组合需指定测试节点验收。

本包含中心服务和节点连接工具，不含原 OCRUN 的完整压测二进制库，不含 sckocp 激活材料，不是 PXE 包。接入节点需已有上述 OCRUN/插件环境。`ocrun` rsync 模块预留只读资源目录，安装时不自动导入原程序树；不要对该空目录执行旧 `ocsync`。节点收尾挂钩只接受现有 0.9.24a 版本，并拒绝静默同步覆盖。已核验的独立安装包可通过 `publish` 发布，发布不会让节点自动升级。

Debian 11 的官方 LTS 已于 2026-08-31 结束（[Debian 发布说明](https://www.debian.org/releases/bullseye/)）。保留其兼容测试不代表仍有官方安全更新；部署需准备可用且签名有效的软件仓库。安装器不会静默关闭 APT 签名或有效期检查。云端测试使用官方安全源，测试仓库调整不进入生产安装器。

此前验证时 Debian 11 安全源索引指向已不存在的 systemd/curl 等包，HTTP/HTTPS 均返回 404，与 [Debian #1147093](https://bugs.debian.org/1147093) 一致。本轮云端尝试从 Debian 官方快照获取当前签名索引中完全同哈希的依赖；实际是否通过见对应版本验证记录。该恢复逻辑仅用于临时云端环境，不写入生产安装器，也不降低 APT 校验。新中心仍需可用的签名软件仓库。

## 在新中心安装：root

选择未运行数据库、Web、rsync 服务的新服务器。准备固定内网 IPv4、允许设备访问的管理 CIDR、正常软件仓库和结果磁盘。下面的 `192.168.50.10` / `192.168.50.0/24` 是示例，须替换；不要填现有生产服务器地址。

把交付的 tar.gz 和 SHA256SUMS 放在 `/root/ocrun-server-materials`。每条代码为一个完整命令；逐条复制。

```bash
cd /root/ocrun-server-materials && sha256sum -c SHA256SUMS
```

```bash
tar -xzf ocrun-server-0.1.3.tar.gz && cd ocrun-server-0.1.3
```

先查看系统依赖方案，不安装：

```bash
bash server_deploy/dependencies.sh --check
```

安装系统仓库依赖；不会替换系统 Python，不重写仓库，也不自动切换已启用的其他 Redis 模块流：

```bash
bash server_deploy/dependencies.sh --install
```

```bash
bash server_deploy/install.sh --address 192.168.50.10 --network 192.168.50.0/24 --check
```

预期 `status=checked`。端口占用、未知已有文件、其他 OCRUN 架构、缺少依赖均会阻止安装。预检不启动服务；只建立并短暂持有安装互斥锁。

```bash
bash server_deploy/install.sh --address 192.168.50.10 --network 192.168.50.0/24 --apply
```

```bash
ocrun-server check
```

预期四个服务 active、数据库认证 PING、HTTP 版本地址和 rsync 模块可访问。`legacy_resource_tree=not_provisioned_by_this_installer` 是明确说明资源程序树未导入，不表示压测工具已分发。

安装器只禁用本次首次安装的软件包附带的默认服务自启，防止下次开机抢占端口；已安装的其他服务保留。数据库原生路径按发行版选择：EL 8 使用 Redis 6 模块，EL 10 使用 Valkey。HTTP/日志服务运行于独立的 `ocrun-server-*` 单元。

启用 SELinux 的系统上，健康检查还会核对服务实际处于 `redis_t/httpd_t/rsync_t` 隔离域，且保留 `NoNewPrivileges`。EL8 安全策略需要补充 systemd 进入 Redis、HTTP 隔离域的两项 `nnp_transition` 规则；不会给通用 `init_t` 增加数据写入权限。它与发行版的 [NNP 域转换机制](https://docs.redhat.com/en-us/documentation/red_hat_enterprise_linux/8/html/8.0_release_notes/new-features#enhancement_security) 一致。

## 任务管理：中心 ocuser

```bash
su - ocuser
```

```bash
occt
```

提供添加、查询、删除队列入口。也可用以下单行命令，主机名必须与指定节点一致：

```bash
ocrun-server task add --node TEST-001 --id DEPLOY-CHECK --task stress=60 --task stress-ng=60
```

```bash
ocrun-server task status --node TEST-001
```

```bash
ocrun-server task delete --node TEST-001 --id DEPLOY-CHECK
```

队列只允许显式删除尚未开始或已交付的批次；运行中或需要恢复的批次会被拒绝。删除队列不会删除结果文件。同名任务重复出现时，原协议只能给同名项目一个时长，时长不同会拒绝入队。未知拼写如 `strss` 在入队前报错。中心写入队列不会启动节点，也不会让空闲节点持续领任务。

0.1.2 的新任务目录仅提供 stress、stress-ng、P95 m1/m2/m4 的四种 ISA 配置、MLC、MBW、cyclictest、UnixBench、SPEC CPU2017；保留 stress/stress-ng 的 _r2 和 cpu 旧别名。sysjitter 不自动重写成 cyclictest，其他旧工具不能新建任务。配套工具 RPM/DEB 需在指定节点明确安装并绑定，见工具套件 MANUAL.md。

## 连接一个指定空闲节点

中心 root 导出私密文件，绑定节点名：

```bash
ocrun-server node-config --node TEST-001 --output /root/TEST-001.connection.json
```

输出不显示密码。用已有 SSH/SFTP 管理连接，把此文件、服务端 tar.gz、0.2.4 收尾安装包及其校验值送到指定节点 `/root`。私密连接文件不能放进 HTTP/rsync 公开资源目录，不能贴到聊天或提交 Git。它只含连接本次新中心的节点角色凭据，不含管理角色密码或 sckocp 激活信息。

指定节点 root：先确认当前无压测、采集和调度进程；如存在，等本批任务完成，不能直接杀掉。先按配套工具套件 `MANUAL.md` 预检并升级收尾组件至 0.2.4，再执行：

```bash
chmod 600 /root/TEST-001.connection.json
```

```bash
tar -xzf /root/ocrun-server-0.1.3.tar.gz -C /root
```

```bash
bash /root/ocrun-server-0.1.3/server_deploy/connect-node.sh --app /root/ocrun --config /root/TEST-001.connection.json --check
```

```bash
bash /root/ocrun-server-0.1.3/server_deploy/connect-node.sh --app /root/ocrun --config /root/TEST-001.connection.json --apply
```

工具校验节点名、root 私密文件、已验证的原 `oc.env` 哈希和收尾版本，备份原文件及属主权限，仅修改服务器地址并加载私密凭据。遇到未知或手工改过的 `oc.env` 会保留现场并拒绝；不要手动忽略检查。已有未完成收尾时不得切换中心。导入不会启动任何任务，重复导入相同文件安全。

节点明确启动本批任务：

```bash
/root/ocrun/mon-sensors-finish start
```

```bash
/root/ocrun/mon-sensors-finish status --human
```

此前原节点的初始化等待、任务监督、收尾、结果核验与成功后的关机策略继续有效。失效的 sckocp 授权仍按原规则拒绝采集。

## 查看结果和发布安装材料

本中心 `/data/cds/result` 指向本地 `/srv/ocrun/logs`，可供 `ocuser` 查看；它不表示已建立原现场 211→221 的 NFS 映射。新部署默认不新增 NFS 服务。

正常结束应收到 `.mon`、`.mon.sckocp.jsonl`、`.xlsx`、`.finish.json`；以节点完成记录中的清单核验 SHA-256。采集质量未知、执行失败、文件交付完成是不同状态，不能只看到 complete 就宣布硬件稳定。

若需内网提供已核验的节点安装包，中心 root 执行以下模板，替换为交付记录中的真实校验值：

```bash
ocrun-server publish --file /root/mon-sensors-finish-0.2.4.run --sha256 REPLACE_WITH_RELEASE_SHA256
```

工具仅发布允许名称的独立安装包，按完整哈希生成不覆盖的资源地址，不执行文件、不改节点。公开资源 URL 返回前会核对输入文件；节点下载后仍需与独立取得的交付 SHA 比对。

同一 publish 命令也接受 ocrun-workloads 的 x86_64 RPM 和 amd64 DEB。仅发布明确指定的文件，不会把授权导入包、原 SPEC 文件或私密连接文件自动发布出去。

## 故障、重试和撤回

```bash
journalctl --no-pager -n 80 -u ocrun-server-db -u ocrun-server-http -u ocrun-server-rsync -u ocrun-server-firewall
```

安装失败会尝试停用本次创建的服务、撤回本次文件及防火墙规则，保留数据库、日志、账户、已装系统包和 `/var/lib/ocrun-server-install` 中的恢复记录及原凭据。修复原因后使用**同一包、同一地址与网段**重新 `--apply`，不重新生成已保存的密码。`rollback_incomplete` 或有手工修改时保留文件，先检查具体差异。

失败回滚前的目录权限、SELinux 标签及服务日志保存在 `/var/lib/ocrun-server-install/failure-diagnostics.txt`（root、0600）。该诊断不包含数据库配置正文或密码。

中心撤回前先让连接它的测试节点完成任务并撤回连接。随后在原解压材料目录以 root 执行：

```bash
bash server_deploy/install.sh --rollback
```

数据、已发布材料、账户、系统依赖和 SELinux 标签规则保留供审计/重装。不会删除其他 nft 表，不清空全局防火墙，不关闭 SELinux。

节点撤回连接：

```bash
bash /root/ocrun-server-0.1.3/server_deploy/connect-node.sh --app /root/ocrun --rollback
```

然后才可按收尾组件手册降回原版本。原 `oc.env` 内容与权限恢复；工具不会自动启动旧调度。

## 安全和支持界限

数据库只绑定指定 IPv4 和回环，保留 `protected-mode yes`，管理凭据与节点凭据分开；节点角色无 CONFIG/ACL/模块/关闭等管理命令权限。仅允许管理 CIDR访问 TCP 80/873/6379，独立 nft 守卫是服务启动依赖；已启用 firewalld/UFW 时只添加对应范围的允许规则。SELinux 按服务类型及独立结果类型标记，不全局开启匿名写入，也不关闭强制访问控制。

原协议仍基于受信任的管理内网。该兼容方案不是多租户隔离：节点角色可操作原任务键，rsync 日志模块沿用共享信任边界。请使用独立管理网；它不应发布到互联网。认证配置是一次性部署步骤，之后不会要求每次手输密码。

本包不声明未经测试的发行版、CPU架构、完整硬件压测、长时间多节点容量、外部NFS布局、发行版之间原地迁移或任意手工配置可用。真实节点接入新中心的最终验收仍由用户在指定空闲节点完成，不能拿旧中心验收记录代替。
