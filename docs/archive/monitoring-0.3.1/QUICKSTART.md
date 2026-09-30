# 接口与设备插件操作手册

适用版本：独立接口 **sckocp-api 0.3.1**，设备插件 **mon-sensors-plugin 0.12.8**。以下命令在需要监控的 Linux 设备上执行；现有 0730 管理服务器保持原状。

| 你准备做什么 | 应阅读的手册 | 需要安装的包 |
| --- | --- | --- |
| 使用现有 0730 系统收集并上传节点数据 | [设备插件操作手册](../../monitoring/MON-SENSORS-PLUGIN-操作手册.md) | `mon-sensors-plugin-0.12.8.run` |
| 让自己的程序读取本机已激活 sckocp 的 JSON 数据 | [独立接口操作手册](../../monitoring/SCKOCP-API-操作手册.md) | `sckocp-api-0.3.1.run` |

插件已经包含接口核心，0730 节点不需要重复安装接口包。两份手册是当前安装包的补充文档，安装包版本及校验值不变。

## 0730 节点最短操作路径

先停止该设备的压测与监控。以下以 root、原设备目录 `/root/ocrun` 为例：

```sh
# 检查安装条件，不修改安装目标
bash mon-sensors-plugin-0.12.8.run --app /root/ocrun --backend sckocp --check

# 安装并明确启用 sckocp 来源
bash mon-sensors-plugin-0.12.8.run --app /root/ocrun --backend sckocp

# 查看帮助、采集一次、持续查看；持续查看时按 Ctrl+C 停止
/root/ocrun/mon-sensors-plugin --help
/root/ocrun/mon-sensors-plugin --once
/root/ocrun/mon-sensors-plugin 5
```

最后三行不会启动压测。采集需要 sckocp 已安装、通过原有授权与平台检查，且具备硬件读取权限。写日志、停止后台监控和沿旧流程上报的方法见插件手册。

## 独立接口最短操作路径

以下以 root、原生程序 `/usr/bin/sckocp` 为例；程序位置不同时替换 `--binary`：

```sh
bash sckocp-api-0.3.1.run --check
bash sckocp-api-0.3.1.run
/usr/local/bin/sckocp-api --help
/usr/local/bin/sckocp-api --version
/usr/local/bin/sckocp-api --binary /usr/bin/sckocp --interval 1 --timeout 20
```

最后一行执行一次采集，输出一行 JSON 后退出；它不会持续刷新或自动上报。正常输出应同时满足退出码 `0` 和 JSON 中 `status="ok"`。Python SDK、错误码及参数范围见接口手册。

## 路径与权限约定

- `.run` 命令应在安装包所在目录执行，或把文件名换成其完整路径。安装完成后保留安装包，升级或切换插件来源时仍可使用。
- 插件路径中的 `/root/ocrun` 必须替换成实际设备目录；原 0730 的 `oc.env`、`APPPATH`、`LOGPATH` 等配置仍需正确。
- `.run` 安装不联网下载依赖；需要 Linux、Bash、tar、SHA-256 工具及 Python 3.6+，插件还要求标准系统路径中有 `python3`。
- 默认安装与原生硬件采集通常按 root 使用；接口和插件不会自动提权。命令帮助本身不需要 root，但调用者必须能访问其目录。
- `--check` 只检查安装条件，不验证激活或硬件读数。安装完成后用手册中的单次采集命令检查实际结果。

分发范围和现有系统兼容边界见 [PACKAGES.md](PACKAGES.md)，已完成的云端验证见 [CLOUD-RESULTS.md](../validation-20260926/CLOUD-RESULTS.md)。
