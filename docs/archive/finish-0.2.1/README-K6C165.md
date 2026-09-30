# K6C-165 节点升级与验收

这是已有 API 0.3.1、插件0.12.9、报表0.1.1、收尾0.1.0节点的升级包。
只升级本节点报表和收尾到0.2.0。保留原sckocp、原管理服务器和原系统。
当前发布所附证据是云端验证；本版实际硬件验收需按以下步骤完成。

## 1. 上传、核验、安装

把 `ocrun-node-update-0.2.0.tar.gz` 上传到 **K6C-165 的 /root**。确认该节点没有正在运行的压测，已有收尾待办已经处理，旧ocb已经退出。安装器会检查并拒绝覆盖运行中的程序；不要用批量kill命令绕过检查。

```sh
(
set -euo pipefail
umask 077
[ "$(hostname)" = K6C-165 ]
[ "$(id -u)" -eq 0 ]
node_update_dir=$(mktemp -d /root/ocrun-node-update.XXXXXX)
tar -xzf /root/ocrun-node-update-0.2.0.tar.gz -C "$node_update_dir"
cd "$node_update_dir/ocrun-node-update-0.2.0"
sha256sum -c SHA256SUMS
bash ./update-node.sh --check
bash ./update-node.sh --apply
/root/ocrun/mon-sensors-finish status --human
printf '材料目录=%s\n' "$PWD"
)
```

安装不启动任务。不适用全新装机，也不自动安装系统Python或sckocp。

## 2. 原工具受信任副本

如果原stress工具由201:200拥有，可明确信任并复制这两个已有工具到插件私有目录，查看清单和SHA后执行第二条。原文件不变，不统一修改ocrun目录属主。

```sh
/root/ocrun/mon-sensors-finish adopt-workloads --task stress --task stress-ng --check
/root/ocrun/mon-sensors-finish adopt-workloads --task stress --task stress-ng
```

## 3. 配置一轮新任务并检查

215控制端沿用原菜单，给K6C-165配置新编号 `RELIABILITY-020`、新的任务时间、stress60秒和stress-ng60秒。

K6C-165 root 执行：

```sh
/root/ocrun/mon-sensors-finish preflight \
  --rdb-server 172.20.8.217 --log-server 172.20.8.211 \
  --log-dir /root/log --node K6C-165 --serial 260168795800086
/root/ocrun/mon-sensors-finish start
```

预检应显示两项任务及时长、磁盘预算，没有取出任务。启动保留原60秒初始化等待。等待整轮结束后查看：

```sh
tail -n 60 /root/ocrun/.mon-sensors-finish/scheduler.log
/root/ocrun/mon-sensors-finish status --human
```

预期同一批次有两项steps，execution分别duration_reached、cleanup_confirmed均true，execution_result=completed、stage=complete。v1数据有效性和读数年龄仍标记未知。

## 4. 控制端复核

215的ocuser在 `/data/cds/result/K6C-165_260168795800086/` 找到本轮 `.finish.json` 和另外三个同前缀文件。读取收尾记录、核对三份数据的SHA256及节点记录中的收尾文件哈希。

将本轮 `status --case CASE_ID` 与控制端哈希结果回传。现场验证不能仅凭start命令成功或stage=complete推断硬件合格。

## 5. 故障和回滚

status --human会列出具体批次和恢复命令。需要停止测试时使用 `stop --case CASE_ID`，不按进程名称批量结束。retry只重新处理收尾，不运行负载；未封存的异常用recover --interrupted。

如果要回到之前已验收的组件组合，在空闲、无未处理批次、旧ocb退出后，回到安装材料目录执行：

```sh
bash ./update-node.sh --rollback
```

原始日志和备份会保留。回滚以后仍需明确启动原任务；不会自动重跑测试。
