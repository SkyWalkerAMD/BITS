# BITS 压测工具套件 0.1.0

本套件用于 x86-64 新压测系统及显式绑定的测试节点。安装不启动压测、不启用服务、不修改现有 215/217/211/221，不删除原工具目录。控制端仍使用原任务协议。先在指定空闲节点验收。

## 工具与版本

|任务|版本|交付|
|---|---|---|
|stress / stress_r2|1.0.7|RPM/DEB 内置|
|stress-ng / stress-ng_r2|0.22.01|RPM/DEB 内置|
|p95-{no,avx,fma3,avx512}_m{1,2,4}|30.19 build 20|一份 mprime，12 种配置|
|mbw|2.0|RPM/DEB 内置|
|cyclictest|rt-tests 2.10|RPM/DEB 内置，替代 sysjitter 任务|
|unixbench|6.0.1|RPM/DEB 内置，无图形测试|
|mlc|3.13|包内自带官方 Linux 程序，无需导入|
|cpu2017（兼容 cpu）|1.0.5|从原始 0730 包导入，保留文件内容|

上游 URL、固定提交、SHA-256、许可与选择理由见 SOURCES.json。mprime 使用 mersenne.org/download 当前推荐的 30.19b20，不自动选镜像目录中较新的 31 系列。其他工具不进入本套件；已有旧节点未绑定本套件时保留原功能。

此版本为工具套件 0.1.0 的第 3 个打包修订（RPM Release=3.el8、DEB revision=3），以 BITS-o 包名交付，接口与安装目录不变。MLC 来自固定 SHA-256 的 Intel 3.13 官方归档，程序原字节、许可 PDF、Linux/redist.txt 和使用文档均保留。使用及后续分发受 Intel 原许可约束，OCRUN 不授予额外权利。SPEC 沿用用户原有授权材料。不会修改 sckocp 或激活状态。

## 安装与绑定（指定空闲测试节点，root）

先核对交付目录的 SHA256SUMS。根据发行版选择一种：

```bash
dnf install ./bits-o-workloads-0.1.0-4.el8.x86_64.rpm
```

```bash
apt install ./bits-o-workloads_0.1.0-4_amd64.deb
```

依赖由系统仓库解决；系统 Python 不替换。离线机器需同时准备发行版依赖包。程序在 /opt/ocrun-workloads/0.1.0，系统已有 stress 等命令不覆盖。EL 8 包基于 EL 8 构建；DEB 基于 Debian 11 构建。目标 EL8/9/10、Debian11/12/13、Ubuntu22.04/24.04/26.04；实际通过级别以交付验证记录为准。RHEL 需订阅及额外验收，不能据 Rocky/Alma 测试推定已验收 RHEL。

```bash
bits-o-workloads check
```

完整 bits-node 已经内置工具库，无需再装独立工具包。旧系统在同一次安装中提供 bits-o-node 和 bits-o-workloads，随后 bits-o setup 自动完成采集、报表、收尾及工具绑定；不需要逐个安装 mon-sensors 组件。完整步骤见 [BITS 部署手册](../deployment/BITS.md)。

独立维护工具绑定时才使用 bits-o-workloads bind --app /root/ocrun --check，核对后去掉 --check。确认节点无活动任务且组件版本匹配后操作；不会启动压测。
## 包内 MLC 与 SPEC 导入

MLC 已位于 `/opt/ocrun-workloads/0.1.0/mlc/mlc`；`bits-o-workloads list` 应显示版本 `3.13`、`delivery=included`、`package_revision=2`。不需要另行下载或导入。许可在 `licenses/mlc/`，使用文档在 `mlc/readme_mlc_v3.13.rst`。

```bash
bits-o-workloads list
```

```bash
/opt/ocrun-workloads/0.1.0/mlc/mlc -h
```

旧 `import-mlc` 命令仍接受同一个固定归档，核验后返回 `included`，不复制第二份程序。已有导入目录保留，但新批次使用已绑定清单中的包内程序；包内文件缺失或被改动时拒绝执行，不悄悄回退到导入副本。

SPEC：把原始 0730 文件复制到指定节点 /root/0730，预留至少 4 GiB 空间。只提取 cpu2017 子树，不执行包中脚本；内部链接转为同内容普通文件，属主规范为 root，拒绝外部链接。既有源包保持不动。

```bash
bits-o-workloads import-spec --archive /root/0730 --check
```

```bash
bits-o-workloads import-spec --archive /root/0730
```

## 运行与结果解释

在新中心添加选定任务，再在该节点执行原收尾组件 preflight/start。一次显式 start 只处理当前批次，不增加空闲循环领任务。预检包括绑定版本、文件哈希、工具允许列表、指令集、可用内存；任何失败发生在任务取出前。

新中心允许的任务只有上表内容。sysjitter、m3、PTU、BC、XMRig 等不会静默映射到其他负载。重复同名任务仍遵守原 Redis 协议的同名同时间限制，运行目录按步骤隔离。

P95 保留 m1=4–88K、m2=150–205K、m4=4–8192K FFT 范围。m4 内存上限取原配置 60962 MiB 与当前可用内存 45% 的较小值；线程数按当前允许 CPU 数量生成。不会保留原包的机器 GUID、pid、联网配置或历史结果。指令集模式只禁用更高指令，不伪造硬件支持。以上改变会影响与旧固定配置的分数比较，应在验收中记录。

cyclictest 测的是定时唤醒延迟，与 sysjitter 的忙循环抖动含义不同，不拼接历史指标。默认 FIFO/80、每允许 CPU 一个线程、1ms 周期，使用自身时长结束以输出统计；监督程序另留 3 秒结束余量。无相应实时调度权限时失败，不自动改内核配置或降成普通调度。容器普通调度冒烟测试不能代替实体机实时延迟验收。

MLC 使用 -e -r，不修改硬件预取 MSR；随机访问结果与旧默认顺序访问测试不能直接比较。MBW 运行 5 轮、每个缓冲区至多可用内存 30%（它分配两个缓冲区）。UnixBench 使用预编译通用 x86-64 程序，不在节点现编译；该构建分数不能直接当作旧 -march=native 构建的对比结论。有限次测试（MLC/MBW/UnixBench/SPEC）的任务时长是最大执行时间，应给足完成整套测试的时间，超时为 timed_out。

MLC 在部分机器上要求预留 2 MiB 大页；本次云端虚拟机要求每 NUMA 节点至少 1000 页。安装器和节点执行器不会自动改 `/proc/sys/vm/nr_hugepages` 或预取器设置。若原程序报告大页不足，该步骤按非零退出记录失败，需由管理员结合可用内存安排后再显式创建新批次，不能视为性能验收通过。云端短时测试仅在一次性 Linux 测试机预留每节点 1024 页并于结束后恢复，这段辅助代码不进入部署包。

原始工具输出按步骤保留在本批次日志与运行目录，执行记录包含程序版本、开始/结束、退出原因与清理结果。SPEC 使用独立 `--output_root`，不会向已校验的导入目录混写本轮结果。监控报表四件套仍按原通道上传和核验；工具原生结果目录目前保留在节点，不列入四件套完成记录。sckocp v1 传感器有效性和读数年龄仍为未知，缺失字段不填 0。

## 回滚

先结束当前批次并解决所有待收尾状态，停止该节点调度。工具安装不会替换原 bin 目录，可解除绑定回到旧工具：

```bash
bits-o-workloads unbind --app /root/ocrun --check
```

```bash
bits-o-workloads unbind --app /root/ocrun
```

需要回退收尾组件时使用 0.2.4 安装器 --rollback（认证节点须先按认证连接手册回退）。RPM 使用 dnf remove bits-o-workloads，DEB 使用 apt remove bits-o-workloads。已导入的授权材料、批次输出和旧工具目录不会被卸载删除。检测到正在运行的负载时包管理脚本拒绝操作，不替用户杀进程。

采集插件如需回到 0.12.9，在空闲节点上使用本次 0.12.10 安装器的 `--modules-only --source` 指向经校验解压的 0.12.9 包，再先预检、后安装；既有调度和报表入口保持原样。也可保留 0.12.10，其日志格式与 0.12.9 相同，仅扩展工具识别。不要用普通旧插件安装器覆盖后加的收尾或报表挂钩。

从旧独立工具包更新时，先在空闲节点解除旧绑定，再安装 0.1.0-4、升级收尾组件 0.3.0，最后显式重新绑定。绑定包含清单哈希，不能直接沿用旧绑定。回滚时同样先解除绑定，卸载新版并安装保留的旧包，按对应安装器回退收尾并重新绑定；不覆盖原工具和结果。完整原生节点包使用 [完整系统手册](../deployment/DISTRIBUTION.md)的 detach 流程，不混装独立工具包。

原生包重装/升级会先核对包管理器记录；发现目录未受包管理或文件被修改时拒绝覆盖。包附 SHA-256 与源码/云端记录，本轮未配置 RPM/DEB 发布签名密钥。校验值用于确认交付内容一致，不能替代独立可信的下载渠道。

## 验证边界

云端负责实际编译、RPM/DEB 安装、动态依赖、短时执行、任务配置隔离、停止清理、篡改拒绝和卸载；MLC 检查原始文件一致性、版本、无需导入的任务入口，以及使用少量内存的短时 idle_latency 测试。具体通过结果以 RELEASE.json 为准。容器共享宿主内核，不能证明所有原生内核、CPU 指令集、传感器、实时延迟或完整 SPEC 分数已验收。MLC 全套硬件测试、SPEC 原包导入及完整运行、P95 各 ISA 模式需指定节点实测；不会触碰 K6C-183 或其他生产机。
