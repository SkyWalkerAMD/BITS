# K6C-165 系统工具链接兼容修正 0.2.1

适用于已安装收尾0.2.0、报表0.2.0、采集插件0.12.9及API0.3.1的OCRUN0.9.24a节点。本包只更新收尾组件，报表和原sckocp保持原版。

## 现场原因

K6C-165上的 `/root/ocrun/bin/stress/stress` 和 `stress-ng/stress-ng` 是201:200所有的符号链接，分别指向root所有、0755权限的 `/usr/bin/stress` 和 `/usr/bin/stress-ng`。0.2.0把链接本身的0777当作普通文件权限拦截，且没有适配这个原版布局。任务没有被取出或执行，RELIABILITY-020可以继续使用。

0.2.1仅为stress/stress-ng及其_r2名称接受明确的 `/usr/bin/` 或 `/bin/` 系统入口。实际程序和完整系统路径仍必须通过root所有权及不可被组/其他用户写入的检查；通过已固定的文件描述符读取，核对读取前后身份。复制结果是私有目录中的普通文件，附带哈希和源系统入口。其它数据链接、未知目标、可写目标、setuid/setgid程序、特殊文件和不安全父目录仍被拒绝。原链接及系统文件不修改。

## 安装

执行位置：**K6C-165，root**。上传包内 `mon-sensors-finish-0.2.1.run` 到 `/root`。确认没有调度、采集、负载或未处理的收尾；安装器会拒绝忙碌节点。无需重启，也不需要重新下发已保留的任务。

以下是一条完整单行命令，可以避免复制时多行被合并的问题：

```bash
bash /root/mon-sensors-finish-0.2.1.run --app /root/ocrun --check && bash /root/mon-sensors-finish-0.2.1.run --app /root/ocrun && /root/ocrun/mon-sensors-finish --version
```

预期最后显示 `mon-sensors-finish 0.2.1`。安装只更新管理代码，不取任务。安装器输出的backup目录应保留，支持恢复前一版的原字节内容。

准备受信任副本，仍是一条完整单行命令：

```bash
/root/ocrun/mon-sensors-finish adopt-workloads --task stress --task stress-ng --check && /root/ocrun/mon-sensors-finish adopt-workloads --task stress --task stress-ng
```

清单应包含每个程序的 `system_target`、字节数及SHA-256，`originals_modified=false`。不会执行压测程序。

预检当前队列：

```bash
/root/ocrun/mon-sensors-finish preflight --rdb-server 172.20.8.217 --log-server 172.20.8.211 --log-dir /root/log --node K6C-165 --serial 260168795800086
```

应为RELIABILITY-020、20260927-11、stress和stress-ng各60秒，工具路径在 `.mon-sensors-workloads` 下。预检不领取任务，确认输出后使用 `mon-sensors-finish start` 开始新一轮现场验收。

## 状态与回滚

0.1.0旧完成记录若没有数据质量字段，现在显示“旧记录未提供质量字段”。旧文件交付验收和传感器有效性仍分别解释，不改写原数据或哈希。

回滚前同样要求空闲并处理完收尾待办：

```bash
bash /root/mon-sensors-finish-0.2.1.run --app /root/ocrun --rollback --check && bash /root/mon-sensors-finish-0.2.1.run --app /root/ocrun --rollback
```

在本节点恢复至原来的收尾0.2.0，报表0.2.0不变；日志和备份不删除。不要用旧的整包回滚脚本代替本次收尾修正回滚，否则还会回滚报表。

## 验证边界

发布包中的测试摘要和RELEASE.json对应源码提交及私有GitHub Actions运行。云端复现201所有的原版链接布局，使用root所有的合成系统程序，验证复制、两项负载生命周期以及拒绝不安全链接；不运行真实硬件压测。K6C-165真实压测仍需用户执行并回传结果。
