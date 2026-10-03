# BITS 独立架构

本实现使用独立调度协议。0.3.0 旧协议与 BITS-o 保留；0.4.0 是独立架构的正式版，云端软件验证与现场硬件验收分别记录。

用户确认的业务方式：网页为主、保留命令行；单个中心约 10–200 台节点；管理员创建批次后明确点击开始；节点完成后保持空闲，只有收到下一次明确授权才执行。节点常驻连接不等于自动领取待办任务。

## 0.4.3：确认后完整卸载

`bits-center uninstall --purge` / `bits-node uninstall --purge` 在本机 root 下执行，与网页批次删除分开。CLI 内嵌 Python 3.6+ 卸载器及相同的停服维护逻辑；先列出角色和路径，再确认。预览不创建事务、停服或删除文件。普通包管理器 remove 仍保留数据。

两个角色维护锁串行化卸载与升级，SQLite 写保留及节点维护锁阻止检查到停服之间进入新任务。先让原生 RPM / dpkg 删除确定角色包，成功后才删除配置和数据；不会自动删除依赖包。包管理器拒绝时恢复原服务；进入文件清理后出现错误，保留 root 私有的 `/var/lib/bits-uninstall/角色/resume.py` 和事务清单，原 CLI 被卸载后也可继续。完成后清除恢复文件。

路径来自现有服务配置、固定角色目录以及明确的 `--include` 安装副本。自定义中心数据目录必须只含可识别的 BITS 数据项；系统根目录、另一角色路径、顶层符号链接及嵌套挂载拒绝清理。记录设备号与 inode，恢复时不删除替换后的目录；递归使用目录描述符和 `O_NOFOLLOW`，内部链接仅删除链接。对共享父目录只做空目录清理。原版 sckocp、网络设置、系统共享日志和未指定的外部副本不在清理范围内。

## 0.4.2：包管理器维护

RPM `%pre` 与 DEB `preinst` 内嵌同一 Python 3.6+ 维护实现，通过前置依赖保证解包前可用。先验证未完成工作，再阻止新任务进入、停服并制作私有完整快照。中心用 SQLite `BEGIN IMMEDIATE` 保护检查到停服的窗口；节点在 poll / 执行 / 空闲关机整个循环持有共享 `maintenance.lock`，安装器持有排他锁后停服。既有任务和未知状态拒绝维护。

不可变的旧节点没有维护锁：仅暂停已验证身份的 systemd 主进程，检查持久执行意图；有未完成工作立即恢复主进程，工作进程不被暂停。确认空闲后，在 systemd 停止事务中终止暂停的旧主进程并等待监控子进程清理，避免恢复旧 poll 后抢到新任务。0.4.2 及之后使用正常的协作停服。

RPM 恢复放在 `%posttrans`，保证不可变旧包的 `%preun` 看到服务已停止。DEB 为旧 `prerm` 的运行中拒绝提供 `failed-upgrade` 回退，再由幂等 `preinst` 接续。脚本顺序依据 [RPM 文档](https://rpm.org/docs/4.20.x/manual/triggers.html) 与 [Debian Policy](https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html)。服务的运行状态与启用状态分开处理；runtime mask 只在本包创建时解除，外部 mask 保留。

备份位于 `/var/backups/bits/{role}/`，事务状态位于 root 私有的 `/var/lib/bits-package/`，均不进入角色数据目录。备份失败在解包前恢复服务；解包或启动失败保留维护状态，重装同版本继续。没有配置的镜像安装可在 systemd 尚未启动时执行；首次 setup / enroll 成功后才启用服务。配置身份、网络和原生授权不由安装器猜测。

## 0.4.1：硬件信息、独立唤醒与永久删除

独立 `POST /node/v1/hardware-info` 由节点凭据限定归属；操作员通过 `GET /api/v1/nodes/{node}/hardware-info` 查看。worker 从现有授权 `info` 结果投影固定章节、CPU 和 DIMM 数据，不新增命令。512 KiB 请求上限、章节/行/字段白名单与 Primary 时序校验在中心再次执行。schema 4 的 `node_hardware_info` 仅保存最新成功快照和最近尝试状态，重启保留；失败不刷新成功快照的时间，重复上报不刷新接收时间。UI 独立标明离线、过期或采集失败。

schema 4 为唤醒操作、每节点进度、手动开机保持和删除进度增加表。升级有节点的 schema 3 数据库前生成 `center.sqlite.before-node-operations-v3`（0600）一致性备份；旧中心拒绝新 schema。

操作员认证的 `POST /api/v1/dispatch/wakes` 接受固定请求 ID 与 1–200 个节点，事务验证所有目标及互斥占用。后台复查 BMC 映射与状态，先持久写开机意图，再调用既有固定 `On` 驱动；已在线跳过，重启后结果不明不重发。每节点等待请求之后的新心跳，10 分钟截止。保持 ID 随节点 poll 下发并持久化，下一次明确开始批次时事务清除；不创建或授权压测。

`POST /api/v1/deletions` 接受固定请求 ID 与 1–200 个批次，完整验证终态后将它们标记为删除中。清理器按上传 / 提交相同的节点锁顺序，使用私有 artifacts 根目录及 `os.Root` 限定删除，清理事件、任务组成员和批次行，再记录完成。结果正文不保留；最小删除标记防止迟到上报 / 上传 / 提交重建证据。中断继续，失败由操作员确认重试。节点遇到自己的删除标记会结束本地交付重试，保留本地文件和计划身份。浏览器必须列出目标并确认，GET 与节点凭据没有写入权限。

相关实现为 `native/wake.go`、`native/delete.go`、`native/web/operations.js`；回归覆盖并发、200 台事务、歧义开机恢复、私有备份、删除路径边界和迟到节点。

## 0.4.0：系统在线时的节点日常监控

常驻节点服务在没有批次采集时运行固定的只读监控工作进程，仍经现有 data-only 接口和原生 sckocp 授权边界。节点只有一个固定的 `monitor/live.json` 快照，不创建批次、日志分段或报告；正常主采样约 2 秒、补充约 30 秒，失败时退避重试。开始批次预检/执行/恢复前取消并等待日常采集退出，执行停止后恢复；不会同时启动两套硬件采集。BMC 自动发现也先暂停日常采集。

认证节点通过 `/node/v1/monitor` 上报有类型限制的会话与样本。中心按节点维护独立的最新快照及最多 180 个汇总点；全局 `/api/v1/live` 的 `monitors` 不含逐核心数组或历史。`/api/v1/nodes/{node}/live` 与 `#monitor-node/节点名` 在压测期间选取当前批次读数，其余时间选取日常读数。原来的批次路径只读该批次缓存；日常更新不能改变它的样本或封存证据。重复/倒退序号和旧会话不会刷新当前读数，新会话重新开始短期趋势。

需要中心与节点同时支持新接口。没有节点系统心跳时，BMC 在线不会让硬件读数显示为实时；无数据或原生权限拒绝时不延用旧值。监控不改变已有的成功交付后空闲关机规则。这项功能随 0.4.0 的中心及节点包交付，同一提交的完整 Linux 矩阵、alpha.7 实包升级与浏览器证据随正式 Release 提供。

## alpha.7：节点接入选择凭据模板

网页添加节点可选择已有自定义 BMC 模板，或在同一流程中创建并选中模板。中心严格验证模板存在、启用和节点名前缀，再在同一 SQLite 事务内写入节点身份与 `node_bmc_profiles` 选择；连接文件保持原有六字段，无 BMC 凭据。明确选择只限制候选模板，不绕过网络、型号、GUID、地址冲突、已有绑定或活动任务检查。停用或不匹配时停止，不向其他模板回退；模板修改不覆盖已绑定凭据。

schema 3 增加上述选择表。升级前对有节点的 schema 2 数据库生成 0600 一致性备份，已有身份和批次保持不变；旧中心拒绝 schema 3，避免忽略明确选择。模板的密码仍只保存在中心私有文件。

## alpha.6：自动 BMC 绑定与状态一致性

节点空闲时通过本机 IPMI 内核接口发现管理口，固定只读命令受单次/总超时与输出大小限制；身份材料通过已认证节点接口上报，不接受目标节点名、凭据或任意命令字段。中心按管理员明确指定的私有 IPv4 网段、节点前缀及主板型号匹配唯一凭据模板，再比较本地与远程 BMC GUID、查询电源，保存绑定。GUID 是映射一致性检查，不是硬件真实性证明；首次引导仍依赖可信节点与管理网络。

模板与核对意图使用中心独有的 0600 原子文件持久化。远程鉴权前记录核对意图；失败不因轮询/重启重复尝试相同模板和发现，未知中断由操作员重试。模板有独立版本，网络查询前后再次检查模板、映射和未完成批次，手工绑定优先。自动绑定后的状态查询及明确授权的开机先重新核对 GUID；身份变化停止电源操作。中心持有全部 BMC 凭据，节点发现不读取 sckocp 激活、授权数据库或账号。

总览接口仅增加无密码的 BMC 状态摘要。运行总览、测试节点和任务分发均区分操作系统心跳与管理口电源状态，过期 BMC 观测不计入可达，较新的关机观测可推翻旧心跳。UI 筛选、计数和节点详情采用同样边界；关机不伪装为节点程序在线。

## 与旧实现的实质区别

| 方面 | 0.3 兼容协议实现 | 独立架构 |
| --- | --- | --- |
| 任务 | Redis 主机映射、TASKS 列表、按名称保存时长 | 不可变批次与独立步骤，每一步可有不同预算 |
| 执行授权 | 节点明确启动调度 | 网页授权，带唯一尝试编号和有效期，节点确认 |
| 数据库 | 网络 Redis / Valkey | 中心本地事务数据库，不对节点开放数据库端口 |
| 通信 | HTTP、Redis、rsync | 统一 HTTPS，节点主动连接，无节点入站端口 |
| 节点身份 | 兼容数据库连接材料 | 单节点凭据；不能读取其他节点批次或操作管理员接口 |
| 原始数据 | 两份对应追加日志 | 带顺序号、步骤编号和 UTC 时间的分段原始记录 |
| 结果交付 | 整文件 rsync | 声明不可变清单、分块续传、完整下载核对哈希、发布回执 |
| 操作 | 命令行与原菜单 | 网页创建/开始/取消/查看报告，命令行使用相同管理协议 |
| 重启 | 检查原调度及恢复记录 | 本地执行意图先落盘；中断后只恢复证据，不重跑压测 |

中心和节点连接层使用 Go；节点采集、工具执行和报告适配仍支持 Python 3.6。采用已验证的工具配置、进程身份检查、受限 sckocp 解析器和流式报告算法，避免丢弃已有正确行为。独立中心和调度没有导入 OCRUN 队列、启动脚本或旧传输实现。

Go 构建固定为 1.27.1；数据库驱动固定 modernc.org/sqlite 1.60.1，依赖清单随源码保存。Go 发布依据见[官方版本记录](https://go.dev/doc/devel/release)，驱动来源见[官方项目](https://github.com/modernc-org/sqlite)。应用二进制使用 CGO_ENABLED=0、GOAMD64=v1；压测工具沿用已发布 0.3.0 中对应 RPM/DEB 的已核验字节，来源和 SHA-256 写入构建清单。

## 模块及数据所有权

- native/model.go：计划、步骤、执行结果、监控质量、报告及交付清单。
- native/store.go：节点、批次和操作事件，SQLite 事务与单节点单批次约束。
- native/dispatch.go / dispatch_api.go：多节点任务组、不可变模板、整组事务授权和幂等操作日志；节点协议仍按单机批次隔离。
- native/power.go：BMC 绑定、固定 IPMI 状态/开机操作、持久化开机意图与有截止时间的系统连接等待。
- native/server.go：身份隔离、网页会话、明确授权、文件交付。TLS 证书需受节点信任，禁止跳过校验和跨站重定向。
- native/agent.go：出站连接、本地意图日志、进程监督、失败恢复、续传与远端回读。
- native/worker/：只在本地运行的硬件工作进程，不监听网络，不读取任务数据库。
- native/web/：随服务端二进制交付的网页，不依赖外部 CDN。
- legacy_plugin/：BITS-o 接入旧 OCRUN；与新版调度协议分开。

中心数据库必须位于本机文件系统。原始证据存储按随机批次编号隔离，上传名称必须属于已封存清单，禁止路径穿越。已有成功交付的清单不能修改。SQLite 的备份需使用数据库支持的一致性方式，不能在运行时仅复制主文件并遗漏 WAL。

## 执行和恢复规则

alpha.5 增加任务组：1–200 台已登记节点共用计划，创建时一次事务生成所有子批次。每个节点仍拥有独立的尝试编号、执行、数据质量与交付结果。一台失败不会被整组“完成”掩盖；组级取消也只记录所选子批次的停止请求。创建/开始/取消各带请求标识，服务端保存规范化请求指纹，拒绝同标识不同内容；响应丢失可原样重试。

节点可达性区分认证心跳（20 秒内）、BMC 电源开/关和不可达。BMC 检查不足以证明 sckocp 或工具就绪，原节点执行前检查仍是必经步骤。不可达节点不能创建新的组内批次；草稿检查后状态变化不会静默跳过。批量开始在事务内重新检查所有所选节点，全部授权或全部不授权。未选中的草稿不会自行进入执行。

关机但 BMC 可达的节点，经明确开始进入 `waiting_boot` 并持有单节点名额；仅发送固定 `chassis power on`，不使用循环断电或重启。开机指令意图先持久化，收到 BMC 应答只变为等待状态，认证节点心跳恢复后才进入 armed。开机/连接期限 10 分钟，失败进入 needs_attention 且 execution=not_started；后续人工处理不会隐式重跑。中心重启发现已记录的开机意图但结果不明时先查询状态，仍关机则待处理，不盲目再发。配置文件只在中心私有目录，固定受信任的系统 ipmitool、清洁环境、密码不出现在 argv；此 BMC 通道不执行远程 Shell 或 sckocp 命令；0.4.3 的系统终端使用独立 SSH 会话。

数据库 schema 2 新增 group_members、dispatch_groups、dispatch_operations、task_templates，并把 waiting_boot 纳入唯一活动批次索引。schema 1 有已有身份/批次时先 VACUUM INTO 生成一致性备份，再事务升级。旧程序拒绝 schema 2，回退使用升级前快照，不能把旧二进制直接连接新数据库。

创建仅保存 draft。点击开始形成 armed 授权；5 分钟内未被节点接受则取消，不在节点重新上线时突然开始陈旧压测。节点把执行意图写入本机后再确认，确认重传使用同一尝试编号。单节点只能存在一个已授权、运行中或待处理批次。

节点重启不重新进入已记录的执行步骤。中断流程检查残留进程，处理已有采集证据；报告与上传的重试不调用压测。无法恢复时保留错误和文件，只有明确关闭未完成记录后才释放该节点。

节点服务使用 systemd 的 control-group 停止范围；正常停止仍由已核对身份的本地执行监督器完成。父进程退出时内核通知工作进程。不能确认残留清理时拒绝将任务标为正常完成。

停止信号处理只设置简单标记，不获取线程事件或条件锁，避免父进程与 systemd 重复通知时发生重入死锁。云端验收在每个目标系统重复三次服务停止 / 重启，并向已核对身份的本批次工作进程追加停止信号；每次都核验清理结果和恢复后没有重复执行。

成功交付、压测成功、硬件合格是不同概念。服务端分别显示 execution、quality、report 和交付阶段；没有建立硬件判定规则时始终标记 not_assessed。监控 v1 的有效性与读数年龄仍为未知。

当前容量边界为每批次 1–128 步、总时长预算不超过 31 天、单文件不超过 16 GiB、交付总量不超过 64 GiB。约每 2 秒采样，每 5,000 行切一个原始记录分段；配置类补充数据约每 30 秒读取一次，保留各次实际观察，不能当作连续测量。报告采用已有流式算法，远端完整回读按 4 MiB 分块。预检估算证据与报告空间，运行时到达 128 MiB 剩余空间保留线即停止本批次并保留失败证据，不删除历史文件腾空间。

## 网页实况与缓存

0.4.0-alpha.7 在节点现有采样之后生成私有的 `live.json` 固定字段投影。Go 节点只读取经过属主、权限及链接检查的本机文件，按批次和尝试编号发布；服务端使用严格类型解码，拒绝任意命令、原始 info、时序及授权字段。心跳与长时间的报表 / 上传操作独立，不产生新任务授权。

中心每节点只保留最近一轮实况、最多 180 个采样；全局接口不携带历史数组，指定批次接口才返回趋势。样本序号倒退、同序号内容变化、未知步骤、未接受的执行和已终结批次更新均拒绝。重复上报不刷新采样接收时间，状态更新不能让冻结样本变新。200 节点读写并发和缓存边界通过 Linux race 测试覆盖；这不代表物理节点吞吐验收。

alpha.3 新增 `bits-live-hardware-v1` 类型投影：插槽和核心指标来自同次 mon JSON，补充指标只从已获准的 mon 概览白名单提取。没有追加原生命令，也不接收时序、授权、任意原始文本。中心检查插槽/CPU 唯一性与归属、有限数值和范围；单条请求上限 2 MiB，最多 256 插槽、8192 个 CPU 编号。硬件数组只保留最新一次，180 点趋势和全节点列表都剔除它；缓存拥有独立副本，读取者不能改变已接受样本。正常 v1 缺失指标保持 null，IRQ 尚未提供。

网页 `#monitor/批次编号` 提供整机概览、插槽卡片、核心矩阵/表格、插槽筛选和每页 64 个核心。状态基于批次阶段、节点心跳、样本接收/观察时间分别判断，结束后保留明确标识的最近读数。新增验证覆盖嵌套字段注入、拓扑/数量/数值拒绝、缓存不变性、数值排序、分页、多插槽界面、失联与独立补充时间；具体通过记录以本提交的云端结果为准。

网页提供总览、节点、批次、报告、操作指引。浏览器按两秒轮询精简实况、约六秒获取列表，后台标签页停止轮询；节点列表分页并采用数字自然排序。长期运行批次不会因为最近 500 个历史记录限制而被隐藏。HTML 的用户输入均通过文本节点渲染，操作沿用会话、CSRF 防护和后端状态约束。

实时展示不是完成证据。缓存不写入完成回执，不替代日志和报告；中心重启会清空缓存。网页明确区分连接时间、主采样时间、约 30 秒的补充信息时间和未知的原生传感器读数年龄。流程变更不触碰 BITS-o、旧 OCRUN 或 sckocp 授权逻辑。

## sckocp 权限边界（保持不变）

BITS 不承担 sckocp 授权服务职责。激活数据库、激活码、授权密钥及权限管理继续位于用户独立 VPS，不进入 BITS 的服务端、节点配置、数据库、日志或报告。

新版仅通过 native/worker/data_api.py 的固定采样入口调用独立本地数据接口。来自网页、任务和节点连接的输入不能选择 sckocp 程序路径、命令、参数或环境。没有激活、授权查询/管理、rmal、许可文件导入、密钥代理或任意命令转发入口。BITS 也不替 sckocp 判断、刷新或签发授权；由原程序对每次数据读取自行判定。

监控与 info 输出继续使用严格字段白名单。只提供 Primary 内存时序；其他时序在数据接口过滤阶段去除，不先存完整输出再在网页隐藏。未提供的传感器明确标记缺失，拒绝用虚假零值补齐。

中心接收的结果文件名也固定为 telemetry 分段、步骤日志、MON/Excel 导出和 HTML/JSON 报告，不是任意文件上传服务。BITS 管理口令和单节点连接凭据只用于 BITS 自身的访问控制，与 sckocp 授权材料没有转换、共享或映射关系。

这里防止的是 BITS 扩大授权访问面，不承诺阻止控制整个操作系统的 root 修改本机程序。原生 sckocp 的授权和防逆向能力仍属于它自身及独立 VPS 的安全边界。

## 验收与后续边界

先在私有 Linux Actions 验证认证隔离、明确开始、重复操作、200 个合成节点的事务竞争、上传中断/篡改、恢复和 Python 3.6。随后测试 RPM/DEB、真实工具短任务、systemd 停止和网页交互。模拟硬件结果不证明 sckocp 的真实传感器或授权服务。

此候选不自动迁移旧 Redis、旧中心存储或 BITS-o 生产节点。多中心、高可用、多人细分权限、企业 SSO、生产证书自动续期和大规模吞吐需后续针对性设计与验收；不以“200 个标识事务测试”冒充 200 台真实硬件并发验收。
## System access (0.4.3)

Schema 5 adds `node_network`, with a private consistent `center.sqlite.before-system-access-v4` backup before migrating a populated schema 4 store. Authenticated nodes report bounded global-unicast interface addresses every 30 seconds; the old heartbeat remains compatible. The operator sees BMC and system addresses separately. SSH targets must match a fresh report from a currently connected, enabled node.

The authenticated center proxies SSH PTYs and SFTP over the node's existing SSH service. xterm 6.0.0 and addon-fit 0.11.0 are bundled offline with MIT attribution. Scripts remain CSP self-only with no eval; inline CSS supports xterm's dynamic renderer. No SSH password, terminal input or output is written to the database or logs. Templates use AES-256-GCM with a separate private center key; this protects stored values, not a compromised center process with access to both files. Public API views never return the encrypted or plaintext password.

Host-key verification runs before password authentication. Trust is pinned to node, literal system IP and SSH port. Changed keys require an explicit reset naming the previous fingerprint. Connections are owned by the authenticated browser session, bounded to 16 center-wide, expire after 30 minutes without input/files or 8 hours, and close on logout/navigation. Output is a bounded 1 MiB sequence buffer; gaps are surfaced, not silently replayed. Credentials/templates for BMC remain independent. This operator-authorized SSH channel is separate from immutable test plans and node workload dispatch.
