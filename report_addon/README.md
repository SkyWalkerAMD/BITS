# OCRUN 离线报表补充包 0.2.0

适用于 Linux x86_64、已有 CPython 3.6 的节点，本次针对 Rocky Linux 8 的 `/usr/libexec/platform-python`。该包处理已有 `.mon` 日志，不执行 sckocp，不采集硬件，不连接授权服务，也不启停压测或调度。

## 安装

在节点以 root 执行：

```bash
sha256sum -c SHA256SUMS
bash mon-sensors-report-py36-0.2.0.run --app /root/ocrun --check
bash mon-sensors-report-py36-0.2.0.run --app /root/ocrun
/usr/local/bin/mon-sensors-report --check
```

`--check` 检查目标路径、包内哈希、解释器和私有依赖，检查原报告入口能否接管，不修改安装目标；`.run` 会临时解包后清理。省略 `--app` 仅安装独立报表命令。

依赖安装在 `/opt/mon-sensors-report/0.2.0/vendor`，入口固定使用安装时的 Python 3.6，并用 `-I -S -B` 隔离系统/用户第三方包和字节码写入。设备不需要 pip 或联网；不新增系统 `python3`，不更改系统 Python 包。

0.2.0 延续了 0.1.1 对 0.1.0 的修正，处理其 将数据文件的单硬链接限制误用于系统 Python 的问题。允许受信任路径下 root 所有、可执行且无组/其他用户写权限及 set-ID 位的解释器具有多个硬链接，安装器只检查它、不写入它。日志、报表、锁文件和安装清单仍保留单硬链接要求；拒绝信息会列出具体路径和相关元数据。0.1.0 预检失败的节点可直接使用本包，无需清理或修改系统解释器；支持已核验0.1.1启动器升级，保留原依赖目录并备份启动器，可用本安装器 --rollback 恢复。未知或被修改的安装拒绝覆盖。

显式 `--app` 会将该应用的 `mon-analyse-log` 原相对软链接替换为受管理的报表转调入口，保存原链接和属主到 `.mon-sensors-report-original.json`。只接管已知、已由 mon-sensors-plugin 适配的 0730/0.9.24a 分析器，不修改其 `py/mon-analyse-log.py`、`oct`、采集脚本或配置，也不升级插件 0.12.9 / API 0.3.1。修改过的未知入口会被拒绝。

## 生成新报表

```bash
/usr/local/bin/mon-sensors-report /root/log/example.mon /root/log/example.xlsx
```

可选第三参数为工作表名，默认为 `Monitoring`。接入后原 `/root/ocrun/oct analyse` 也可使用；它仍选择日志目录里修改时间最新的 `.mon`，默认输出 `${HOSTNAME}-Analyse.xlsx`，不转发额外参数。首次验收建议用独立命令明确指定刚验证的日志和新报表名。

不超过2MiB的小报表延续原分析器的数据表、任务均值汇总和九张曲线图，保留不可用指标的空列、保留来源文本；任务标签等文本不会自动变成公式或超链接。使用私有工作目录生成成功后再原子发布，输出权限为 0600；输入日志不修改。

同名工作簿重新生成时，原内容先备份到输出目录下 `.mon-sensors-report-backups/`，再替换。备份不会自动删除，需要按实际保留要求管理。拒绝软链接、硬链接、特殊文件、不可信路径和并发写同一输出；大报表上限16GiB、400万条11列数据，使用流式处理，每25万条拆分一个工作表，生成任务统计和每1000条均值的趋势图。原始数据完整保留，空值不计入均值；不把汇总曲线当逐采样原始曲线。原报表布局仍用系统运行时长列，并非当前压测任务的计时。

生成在原 `/root/log` 下的 `.xlsx` 可沿 `/root/ocrun/oct push` 上传。独立包不修复旧管理服务器自身的 Excel 分析器；服务器读取节点生成的报表即可。现场已经验证旧上传通道接收 `.mon`/JSON 后 ocuser 可读，新增 Excel 仍应验证实际上传和读取。

## 恢复原报表入口

```bash
bash mon-sensors-report-py36-0.2.0.run --app /root/ocrun --detach --check
bash mon-sensors-report-py36-0.2.0.run --app /root/ocrun --detach
```

恢复原软链接及其属主，保留独立报表命令、私有依赖和所有日志/报表。原入口恢复后仍依赖其原有 `python3` 及报表环境。原 `ocsync` 可能覆盖节点入口，完成原同步后可重新应用补充包。安装不会自动恢复已暂停的 ocb 调度。

## 构建与边界

包由授权的 GitHub Actions Linux 工作流构建。固定 NumPy 1.19.5、pandas 1.1.5、openpyxl 3.0.10、XlsxWriter 3.0.3 及四个传递依赖，用于兼容 Python 3.6。原始 wheels 从 PyPI 下载并核对其 SHA-256，来源记录在 `PROVENANCE.json`，原许可文件随依赖保留。参考：[pandas 1.1.5](https://pypi.org/project/pandas/1.1.5/)、[NumPy 1.19.5](https://pypi.org/project/numpy/1.19.5/)、[openpyxl 3.0.10](https://pypi.org/project/openpyxl/3.0.10/)、[XlsxWriter 3.0.3](https://pypi.org/project/XlsxWriter/3.0.3/)。

这是为既有 EL8 节点制作的兼容包，不声称支持所有 Python/CPU 架构。它不新增网络服务，也不保证处理任意不可信 Excel 输入：入口只读取受限 `.mon`，原工作簿备份按字节复制，不解析其内容。哈希可检查完整性，不代替可信分发渠道或发布者签名。实际云端运行结果以交付的验证记录为准。
