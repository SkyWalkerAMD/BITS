# BITS 独立架构候选部署

版本 0.4.0-alpha.1，使用全新测试服务器和空闲节点。安装器不会启动服务或压测。此文是候选操作说明，已验证范围以同源码提交的 Linux Actions 结果为准。现有 215/211/217/221、BITS-o 和稳定版 0.3.0 不自动升级。

## 需要安装什么

中心安装 bits-center 的 RPM 或 DEB；节点安装 bits-node 的 RPM 或 DEB，节点包包含固定版本工具及本地采集/报告适配器。节点仍单独安装原版 sckocp，并在 BITS 以外按原流程完成授权。BITS 不保存激活码，不连接或管理激活数据库。

候选 RPM 版本为 0.4.0-0.alpha.1.el8，DEB 为 0.4.0~alpha.1-1；程序显示 0.4.0-alpha.1。不同包格式遵循各自预发布版本排序。先核验 SHA256SUMS，再用系统包管理器安装对应格式。不要在已有生产节点上强制覆盖。

## 新中心，root

查看已配置的网络地址：

    bits-center network

选定现有内网地址和允许网段，先看计划，再创建配置：

    bits-center setup --address 192.168.50.10 --network 192.168.50.0/24 --check
    bits-center setup --address 192.168.50.10 --network 192.168.50.0/24 --apply
    systemctl enable --now bits-center.service

默认仅使用 TCP 443。应用本身校验来源网段；已有主机防火墙仍需允许该网段访问 443，候选不会修改网卡、网关、DNS 或关闭防火墙。节点不需要开放入站端口，数据库不监听网络。不需要 Redis 6379、rsync 873 或 HTTP 80。

管理员连接材料保存在 /root/.bits/admin.json，仅 root 可读，其中 token 是 BITS 管理口令，与 sckocp 激活码无关。浏览器访问中心 HTTPS 地址，用该管理口令登录。首次生成的是专用自签证书；通过可信渠道核验并安装证书信任，不能忽略证书验证。节点导出的连接文件固定信任该证书；更新证书需要明确重新分发信任材料。

## 网页登记节点

点击“添加节点”，填写实际 hostname 和稳定资产编号。下载该节点专属连接文件，使用可信 SSH/SFTP 等方式传到节点 /root，设为 root 属主、0600 权限。不要将该文件放 Git、公共下载目录或结果目录。

也可用命令行：

    bits-center node-add --node TEST-NODE --serial ASSET-001 --keep-on --output /root/TEST-NODE.bits.json

没有 --keep-on 时，成功交付后沿用 30 分钟空闲关机策略。登录用户、运行任务或待处理失败阻止关机；断网不触发关机。

## 新节点，root

安装 bits-node 包后：

    bits-node enroll --file /root/TEST-NODE.bits.json
    bits-node check
    systemctl enable --now bits-node.service

服务只等待明确授权，不从待办列表自动执行压测。原 sckocp 的交互硬件查看仍使用 sckocp；BITS 不提供新的硬件交互命令或远程 sckocp 控制入口。

## 开始与结果

网页创建批次，选择工具和每步时长。创建后先等待，核对节点再点击“开始”。初验建议 stress 10 秒 → stress-ng 10 秒；这会实际压测，只能用于指定空闲节点。

命令行等价操作：

    bits-center batch-add --node TEST-NODE --label FIRST-CHECK --steps stress=10,stress-ng=10
    bits-center start --batch 输出的批次编号
    bits-center status

批次内同工具可以重复出现且使用不同预算。开始授权 5 分钟未接受会取消；重启不会自动执行已过期授权或重复已有尝试。取消需指定原因：

    bits-center cancel --batch 批次编号 --reason "操作员取消本次验证"

网页分别显示压测执行、监控质量、报告和交付。节点保留带序号及步骤编号的 telemetry 分段、工具输出、MON 导出、Excel、HTML/JSON 报告；中心核验文件后生成独立 receipt.json。上传支持分块续传，节点仍对远端完整副本核验 SHA-256。非 Primary 时序和授权材料不得出现在任何交付文件中。

节点状态：

    bits-node status
    journalctl -u bits-node.service -n 60

有待报告/上传步骤时，常驻服务只重试证据处理，不重新运行压测。无法恢复的批次保留现场，由操作员确认节点工作进程已停止后，使用网页“关闭未完成”并填写原因；不会产生成功回执。

## 停用、保留证据与回退

先取消当前批次并确认已结束，再停止对应服务。未确认的进程残留不能通过强制卸载绕过。

    systemctl disable --now bits-node.service
    systemctl disable --now bits-center.service

分别在对应机器执行，然后使用 dnf remove 或 apt remove 移除对应角色包。配置及 /var/lib/bits 中的数据库、节点意图和证据保留。不要把新版数据库文件覆盖到旧版中心；回退采用旧版原服务器或独立保留的旧配置/数据快照。本候选的包管理安装检查不能代替数据库一致性备份和生产迁移方案。
