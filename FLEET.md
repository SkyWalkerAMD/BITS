# 批量管理与设备总览

以下操作在中心服务器执行；客户端仍只能访问自己的任务和状态。示例使用安装后的 `ocrun-admin`。首次使用请先完成设备登记。

## 分组、模板与批量任务

准备 `burn.json`。可在同一文件中加入经过验证的 `protection` 和 `acceptance` 配置；未配置验收规则时不会给出“硬件通过”的结论。

```json
{"tool": "cpu-burn", "duration_seconds": 600, "threads": 1}
```

```bash
sudo ocrun-admin group-set rack-a node01 node02 node03
sudo ocrun-admin template-set burn-10m --file burn.json
sudo ocrun-admin batch-create burn-001 --group rack-a --template burn-10m --max-parallel 2 --stagger-seconds 30
sudo ocrun-admin batch-run burn-001
```

`batch-create` 保存分组和模板的快照，不立即下发。之后修改模板不会改变已创建的批次。计划文件存放在中心 `data_root/fleet`，每台设备的任务编号在首次下发前就已保存。

`batch-run` 是前台控制器：需保持进程运行，也可以由自己的服务管理器托管。退出后已下发任务会继续运行；再次执行同一命令恢复调度，不会重复下发已经完成或中断的任务。`--once` 只执行一轮调度，适用于外部调度器。同一中心同时只允许一个控制器进程运行。

并发上限针对**该批次中已下发但尚未结束的任务**，包括排队、执行中和正在确认下发结果的任务；不包含手工提交的任务或此前其他批次仍在运行的任务。离线设备的未结束任务继续占用名额，不会因心跳消失就补发新任务。等待下发的设备必须在线、未暂停且没有其他排队或执行中的任务。错峰间隔控制下发时间；客户端实际启动时间还受轮询和网络延迟影响。

```bash
sudo ocrun-admin batch-status burn-001
sudo ocrun-admin batch-cancel burn-001
```

批次取消会先保存不可丢失的取消请求：尚未下发的任务停止下发，排队任务直接取消，执行中的任务收到停止请求后由客户端结束并回传结果。网络中断或控制器正在忙时，需要继续运行同一批次的 `batch-run`，直到状态变为 `cancelled`；收到取消请求不等于所有设备已经停止。

如果取消恰好发生在一次网络下发过程中，这个任务仍可能先被设备领取；控制器拿到下发结果后立即对同一任务发出取消，并停止后续下发。远端已运行但状态记录不完整的设备会继续视为占用，不会被当成空闲设备复用。

## 单任务取消与暂停领取

```bash
sudo ocrun-admin cancel node01 TASK_ID
sudo ocrun-admin pause node01
sudo ocrun-admin resume node01
sudo ocrun-admin pause --group rack-a
```

暂停只阻止领取新任务，当前压测会继续。取消命令同时支持排队任务和执行中的任务，取消已结束的任务不会误伤下一任务。需要确认远端工作负载已停止后，才能使用原有的 `resolve-interrupted --confirmed-stopped` 人工处理失联任务。

## 有上限的历史查询

```bash
sudo ocrun-admin status node01 --offset 0 --limit 20
sudo ocrun-admin status --group rack-a --state failed
sudo ocrun-admin reindex-history
```

任务按创建时间从新到旧返回，默认每台 20 条，最多 200 条。`--state` 只过滤当前页，空页不代表其他页没有匹配任务；按输出的 `next_offset` 继续翻页。并发提交新任务时，基于偏移量的分页可能重复或略过正在移动的记录，导出批次报告前宜等待本批次结束。

旧版任务仍保留在 Redis 中，但升级后需要显式执行 `reindex-history` 建立历史索引。未迁移时输出 `history_incomplete: true`；当前运行任务仍单独显示。查询不会隐式扫描全部历史。

分页限制的是查询和导出的开销，**不会自动清理中心历史**。当前 Redis 任务记录、批次计划和中心日志仍持续保留；应监测中心磁盘／Redis 占用并备份。客户端日志保留期不影响中心保留策略，尚未提供中心历史自动归档清理。

跨设备操作默认选择前 200 台设备，通过 `--host-offset` 和 `--host-limit` 分页，单次最多 1000 台；输出包含实际选择数量和总数量。这一限制同样适用于 `pause`、`resume`、`reindex-history`、设备总览和 CSV 导出。

## 离线设备总览和 CSV

```bash
sudo ocrun-admin dashboard --group rack-a --output /tmp/rack-a.html
sudo ocrun-admin export --group rack-a --limit 100 --output /tmp/rack-a.csv
```

HTML 是可直接用浏览器打开的只读快照，显示在线状态、暂停状态、当前任务、结果、上传错误以及最近温度、CPU 忙碌比例和功耗曲线。不会启动网络服务，不包含设备凭据，也不会从页面控制设备。重新生成文件后才会更新；为避免误覆盖，输出路径必须尚不存在。

当前数值来自最近心跳携带的采样，曲线来自已上传文件；两者的时间分别显示，不能假定它们是同一时刻的数据。设备离线时仍可查看之前的任务与已收齐日志。

曲线来自服务器**已经收到的日志**，可能滞后于当前设备状态。每个任务最多读取末尾 1 MiB、展示最近 240 条样本；横轴为采样顺序，缺失读数留空。CSV 只导出所选设备与历史页，不应将单页导出误认为全部历史。

## 上传确认与客户端保留期

中心的日志校验服务会核对收到的文件清单与校验值，再确认该任务已完整上传。也可以手工执行一轮：

```bash
sudo ocrun-admin verify-logs
```

输出中的 `verified` 表示本轮新增确认数量，`pending` 表示尚不完整或尚未通过校验的任务数量。确认记录保存在该设备自己的 Redis 命名空间。

新登记客户端的 `local_retention_days` 默认为 `null`，保留本地日志。只有明确设置不少于 1 天的保留期后，客户端才会清理**已获中心确认、超过保留期且本地校验仍一致**的日志；活动任务和未确认日志不会被清理。`cancel_poll_seconds` 默认为 2 秒，代表正常联网时检查停止请求的间隔，不是断网设备的停止时限。
