# 工具更新：指定测试节点验收

材料用于新中心与一台明确选定的空闲节点，不需要在 215/217/211/221 上升级。以下节点命令使用 root；中心添加任务使用 ocuser。完整依赖、导入、回滚和版本说明见 MANUAL.md / SERVER-MANUAL.md。

## 1. 安装材料

把总包和 .sha256 复制到测试节点 /root，逐条执行。解压只写新目录。

```bash
cd /root && sha256sum -c ocrun-toolkit-0.1.1.tar.gz.sha256
```

```bash
ocrun_materials=$(mktemp -d /root/ocrun-toolkit.XXXXXX) && tar -xzf /root/ocrun-toolkit-0.1.1.tar.gz -C "$ocrun_materials" && cd "$ocrun_materials/ocrun-toolkit-0.1.1" && sha256sum -c SHA256SUMS
```

确认当前批次已结束、没有待恢复收尾，停止的是这台节点的空闲调度。安装器发现仍在运行的任务会拒绝，不要绕过检查或按名称杀进程。保留当前材料目录，先按 MANUAL.md 安装对应 RPM/DEB；已有节点先更新插件的 `--modules-only`，然后安装收尾 0.2.4、绑定工具套件。

```bash
ocrun-workloads check
```

```bash
/root/ocrun/mon-sensors-finish check --scheduler
```

预期工具版本与清单相符，收尾无 pending。这些命令不启动压测。

Debian 11 若当前官方源仍出现已知 404，可只在 Debian 11 amd64 上使用随包 `debian11-dependencies`；各依赖的版本、来源及当前签名索引 SHA-256 记录在 PROVENANCE.json。先通过总包 SHA256SUMS 后执行：

```bash
apt install ./debian11-dependencies/*.deb ./ocrun-workloads_0.1.0-2_amd64.deb
```

该依赖集用于当次云端 Debian 11 基线，不覆盖任意定制镜像或中心全部依赖；APT 报告冲突/降级时停止，不能加允许降级或关闭签名的选项。

## 2. 第一批：新中心 ocuser

完成 SERVER-MANUAL.md 中的新中心安装与指定节点连接后，把 TEST-001 换成实际节点名。只下发一次。

```bash
ocrun-server task add --node TEST-001 --id TOOLS-010 --task stress=60 --task stress-ng=60 --task p95-no_m1=60 --task p95-no_m2=60 --task p95-no_m4=60 --task mbw=120
```

## 3. 明确启动：指定节点 root

```bash
/root/ocrun/mon-sensors-finish start
```

```bash
/root/ocrun/mon-sensors-finish status --human
```

预期约 60 秒初始化，再顺序执行本批。定时项目达到时长；MBW 是有限次测试，在最大时间内正常退出即可。各 steps 有新的 tool_version、实际时长、退出原因和 cleanup_confirmed=true；收尾 complete，sckocp v1 仍标注读数质量未知。中心收到四份监控文件并按 finish.json 核对哈希；工具自身文本和原生结果保留节点，不冒充四件套已覆盖。

## 4. 后续独立验收批次

第一批通过后再安排 cyclictest、UnixBench、MLC、SPEC：

|项目|任务时长含义与验收重点|
|---|---|
|cyclictest=60|需真实 FIFO/80 调度权限；核对 cyclictest.json 的延迟统计和正常结束，容器权限不足记录不算通过|
|unixbench=3600|时长是上限；完整测试与分数必须正常输出，不能用云端单项 pipe 冒烟替代|
|mlc=1800|包内已含 Intel 3.13，无需导入；核对实际硬件兼容性和输出，-e -r 与旧默认测试不可直接对比分数|
|cpu2017=86400|先导入原 0730；核对编译器/运行库、原配置及完整结果；--output_root 位于本步骤独立目录|
|P95 AVX/FMA3/AVX512|按 CPU 实际支持选择，各 ISA 与长时稳定性分别验收|

具体时间是验收起点，不保证所有硬件在上限内跑完整套基准。有限次测试超时必须记录 timed_out；不要只凭存活到时间上限宣布成功。

## 5. 回传及回滚

回传：节点/中心发行版、工具 check、当前批次 status --case 的 JSON、中心四文件哈希和工具输出摘要。无需贴激活码或数据库私密连接文件。

遇到执行失败先保留步骤输出；报表/上传失败使用已有恢复入口，只重做未完成收尾。回滚顺序：完成收尾并停止该节点调度 → 工具 unbind → 必要时撤回新中心连接 → 收尾安装器 --rollback → 如需，卸载原生工具包。原工具目录、导入材料和结果文件保留。详见 MANUAL.md。
