# 新中心部署：最小现场验收

仅用于**一台新中心＋一台明确选定的空闲测试节点**。现有 215/217/211/221 与其他生产节点不操作。Debian 11 当前有上游软件源阻塞，暂不作为首台现场服务器。

本轮统一交付为 `ocrun-toolkit-0.1.1.tar.gz`，其中包含中心包 `ocrun-server-0.1.2.tar.gz`、收尾安装器 `mon-sensors-finish-0.2.4.run`、工具 RPM/DEB、手册、`RELEASE.json` 和云端证据。根目录 `SHA256SUMS` 覆盖全部材料；`.tar.gz.sha256` 校验外层总包。不要把私密连接文件加入交付材料或结果共享目录。

把总包和同名 `.sha256` 放在新中心 `/root`，以 root 核验并解压到新建目录（不覆盖旧材料）：

```bash
cd /root && sha256sum -c ocrun-toolkit-0.1.1.tar.gz.sha256 && ocrun_materials=$(mktemp -d /root/ocrun-server-materials.XXXXXX) && tar -xzf ocrun-toolkit-0.1.1.tar.gz -C "$ocrun_materials" && cd "$ocrun_materials/ocrun-toolkit-0.1.1" && printf '材料目录=%s\n' "$PWD"
```

## 1. 新中心 root

在解压后的交付目录核对材料：

```bash
sha256sum -c SHA256SUMS
```

按总包 `SERVER-MANUAL.md`（中心子包 `server_deploy/MANUAL.md`）安装本系统依赖，然后使用新中心真实 IPv4 和管理网段进行预检、安装。不要原样使用手册示例地址。

```bash
ocrun-server check
```

预期 `status=ok`、四个服务正常、`database_authentication=required`。它不代表原版压测工具已安装到新节点。

在正式下发任务前，重新启动**这台新中心**一次，再执行同一检查，确认服务与访问限制可恢复。本步骤不要在旧中心执行。

## 2. 指定测试节点 root

前提：已具备现场基线 OCRUN 0.9.24a、API/插件/报表和可用的已激活 sckocp；没有正在运行的任务、调度或未完成收尾。节点名以 `hostname` 为准，后续统一使用这个名称。

按总包 `MANUAL.md` 将收尾组件升级到 0.2.4，并安装、绑定选定工具包。按中心手册导出与此节点名匹配的私密文件，经 SSH/SFTP 传给该节点，完成 `connect-node.sh --check` 和 `--apply`。导入不会启动任务。若预检拒绝未知文件或发现未完成任务，把错误保留给开发人员，不要跳过检查。

## 3. 新中心 ocuser

以下 `TEST-001` 必须替换为指定节点的实际主机名。批次只下发一次，不需要重复删除和重建。

```bash
ocrun-server task add --node TEST-001 --id SERVER-DEPLOY-CHECK --task stress=60 --task stress-ng=60
```

```bash
ocrun-server task status --node TEST-001
```

预期两项任务均为 60 秒、队列处于 pending。该操作本身不让节点开始运行。

## 4. 指定测试节点 root

```bash
/root/ocrun/mon-sensors-finish start
```

```bash
/root/ocrun/mon-sensors-finish status --human
```

原初始化等待约 60 秒，随后两项任务各运行约 60 秒并自动收尾。预期执行 `completed`、收尾 `complete`、没有遗留工作进程；sckocp v1 的有效性和读数年龄仍应标为未知。成功后的原自动关机策略继续有效。

用状态输出的批次编号获取精确记录，不要复制其他批次的哈希：

```bash
/root/ocrun/mon-sensors-finish status --case REPLACE_WITH_CASE_ID
```

记录两项任务的实际时长、退出原因、`cleanup_confirmed`、三个数据文件与完成记录的 SHA-256。提前退出、清理失败、报表失败或上传失败均不应被当作成功。

## 5. 新中心 ocuser

```bash
ocrun-server task status --node TEST-001
```

预期任务状态为 delivered。在 `/data/cds/result/实际主机名_实际序列号/` 找到本批 `.mon`、`.mon.sckocp.jsonl`、`.xlsx`、`.finish.json`，逐个计算 `sha256sum` 并与第 4 步的**同一批次**记录比对。以普通 ocuser 能读取且四份文件一致作为交付验收。

## 撤回与故障范围

收尾失败使用现有组件的状态/恢复入口，只恢复未完成步骤，不重新下发压测。不要断开生产网络模拟故障；云端已单独测试故障路径。

撤回顺序：测试节点任务完成、调度停止 → 节点连接工具 `--rollback` → 如需要，再撤回收尾升级 → 最后在新中心原材料目录执行中心安装器 `--rollback`。中心日志、数据库与账户保留；节点原服务器地址和 `oc.env` 属主权限恢复，不自动启动原调度。详见 `MANUAL.md`。

回传材料只需：新中心系统版本、安装/重启检查、节点本批状态、四份文件比对结果。不要回传 `connection.json`、`connection.env` 或 `/etc/ocrun-server/server.json` 中的密码。
