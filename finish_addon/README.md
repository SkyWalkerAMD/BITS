# mon-sensors-finish 0.2.1

0.2.1补充原版stress/stress-ng系统程序符号链接的安全接入，单独升级收尾组件并支持回滚至0.2.0。现场命令见 [README-LINK-FIX.md](README-LINK-FIX.md)。报表版本保持0.2.0。

OCRUN 0.9.24a 节点任务执行与收尾扩展。保留原控制服务器、原sckocp与本地接口，不做空闲轮询。

本轮完整行为、兼容边界和命令见 [OPTIMIZATION.md](OPTIMIZATION.md)，现场升级与验收见 [README-K6C165.md](README-K6C165.md)。

- 原子检查和领取当前批次，持久化领取意图，保留重复项目时长。
- 受监督的定时负载，提前退出与中断单独记录，按进程身份清理本轮会话。
- 监控健康和磁盘保护；统一停止采集、核验日志、生成报表、上传及下载SHA256核对。
- 报表0.2.0以流式方式处理长日志；原始数据和四种结果文件保持兼容。
- status --human、status --case、start、stop --case、retry、recover --interrupted。
- 已识别0.1.0升级与回滚；版本变化阻止原自动同步覆盖；保留原正常关机策略。

版本通过云端隔离测试以后才由 release.py 生成升级包。现场基线0.1.0的正常流程验收不自动等同于0.2.0的现场验收。
