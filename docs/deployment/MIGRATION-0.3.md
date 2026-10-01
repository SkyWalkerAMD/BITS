# BITS 0.3 目录迁移与回滚

适用于明确选定的完整 BITS 节点，root 执行；旧 OCRUN 节点继续使用 bits-o，不能使用本迁移入口。先保留原版本 RPM/DEB、SHA256SUMS、连接材料和记录。以下命令只在安排好的空闲节点执行。

## 节点迁移

旧节点先查看 `bits-node status --human`。未完成批次必须在旧版本处理 `retry`、`recover --interrupted` 或有理由的 `close-incomplete`；迁移不会继续或重跑它们。

```bash
bits-node stop-scheduler
bits-node detach --check
bits-node detach
```

保存最后返回的 `backup` 绝对路径。确认原包校验文件保留后，用包管理器移除已分离的 bits-node，再安装 0.3.0。不要使用强制覆盖。0.2.2 的旧包/命令叫 ocrun-node，需先显式移除。

下面示例中的 `detached-实际目录` 必须替换为刚才的路径：

```bash
bits-node migrate --from /var/lib/ocrun-node/detached-实际目录 --check
bits-node migrate --from /var/lib/ocrun-node/detached-实际目录
bits-node check
bits-node status --human
```

导入校验旧身份、配置、程序清单和完成结果哈希；只接受 complete / closed_incomplete。新索引位于 `/var/lib/bits/node/app/state`，旧状态原文及哈希另存于 `/var/lib/bits/node/migration-history`。原日志仍在 `/var/log/ocrun-node`，不改写、不挪动。重复迁移安全，批次冲突拒绝覆盖；新任务写入 `/var/log/bits/node`。

支持检查的旧配置版本为 0.2.2–0.2.5；实际发布包的迁移/回滚验证范围以 RELEASE.json 和云端记录为准，不能将接受版本清单当作全部已验收。外置 SPEC 原文件保持不动，新工具目录需通过 `bits-node tools import-spec` 再次校验导入。

## 节点回退

先处理新版本的未完成批次，执行新版本的 stop-scheduler 和 detach，保留它返回的新备份。卸载 0.3.0，安装已保存且校验通过的原版本包；确认原 `/etc/ocrun-node/native.json`、`connection.json` 及 `/var/lib/ocrun-node/app` 均不存在后，将**原版本 detach 备份**中的 app、native.json、connection.json 按原路径恢复并保留属主/权限。禁止把新版本状态复制到旧程序中。

在维护终端核对备份路径、原文件清单和目标不存在后再复制；若目标已有内容，保留现场并停止。恢复后执行旧版本 `bits-node check` 和 `bits-node status`，核对封存哈希。旧版本回退不会自动领取任务，新版本新增的报告留在 `/var/log/bits/node`；其状态留在新版本备份中。

## 管理端和旧系统增强包

管理端 0.3.0 使用 bits 账户、bits-center-* 服务、`/etc/bits/center`、`/srv/bits/results` 和独立数据库目录。0.2.x 中心的数据库/结果迁移不自动执行：须先查清实际存储和活动节点，安排一致性备份及停机窗口，确认账户权限和凭据切换。安装器遇到旧 manifest 会停止；仅卸载旧包并配置新中心不等于恢复了旧数据。

bits-o 0.2.0 保留原 OCRUN 目录及 occt 协议。用原版本 `bits-o rollback` 和 `bits-o unconfigure` 显式分离后再更换包，保留接入日志与各组件备份，随后按 [旧系统接入说明](BITS.md) setup。旧控制机与新中心不能混用角色包。

## 现场验收

仅在一台选定节点新建 stress → stress-ng 各 60 秒批次。检查新目录、两项 duration_reached / cleanup_confirmed、未知传感器质量说明、六份文件哈希和旧记录可读。时序仅含 Primary；接口不接受 rmal。云端传感器夹具不能替代此项硬件验收。
