# mon-sensors-finish 0.2.4：认证接入与包内 MLC

保留原数据库认证接入；本版增加工具套件 0.1.0-2 的包内 MLC 3.13 选择。已有 215/217 中心和未配置认证文件的节点继续使用原方式；不会自动切换服务器、启动调度或改变关机策略。使用新工具包时须在空闲节点解除旧绑定、更新、再显式绑定，详见工具手册。

- 数据库读取 `/etc/ocrun-node/connection.json`，必须为 root 拥有的 0600 普通单链接文件，父目录不可由其他用户写入。
- 凭据只传给匹配服务器地址、端口、节点名的数据库客户端；不放入命令参数、任务状态或日志。
- 原有 Redis 键、Lua 领取确认、执行监督和收尾流程保持一致。
- 支持从已核验的 0.1.0 / 0.2.0 / 0.2.1 / 0.2.2 / 0.2.3 升级，遇到手工修改拒绝覆盖。

在指定的空闲节点以 root 执行，每条为单行；安装前先核对交付包 SHA256SUMS。

```bash
bash /root/mon-sensors-finish-0.2.4.run --app /root/ocrun --check
```

```bash
bash /root/mon-sensors-finish-0.2.4.run --app /root/ocrun
```

然后按服务端包 `server_deploy/MANUAL.md` 导出并导入私密连接文件。没有收到新服务器地址和专用连接文件时，不执行连接切换。

撤回顺序：先用 `connect-node.sh --rollback` 恢复原中心连接，再撤回组件。不要在保留认证配置时降回不支持该配置的 0.2.1。

```bash
bash /root/mon-sensors-finish-0.2.4.run --app /root/ocrun --rollback --check
```

```bash
bash /root/mon-sensors-finish-0.2.4.run --app /root/ocrun --rollback
```

真实硬件基线是 Rocky Linux 8.10 / platform-python 3.6.8 / sckocp 1.2.0 / API 0.3.1 / 插件 0.12.9 / 报表 0.2.0 / 收尾 0.2.1。本版云端结果见交付 RELEASE.json；服务器发行版测试不会自动扩展设备端硬件支持范围。
