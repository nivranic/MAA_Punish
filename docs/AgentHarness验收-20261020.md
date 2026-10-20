# Agent Harness 验收记录（2026-10-20）

本记录是当前交付的证据快照，不是后续任务必须重复执行的清单。长期入口为 [AGENTS.md](../AGENTS.md)，项目知识与命令维护在 [开发与验证指南](开发与验证指南.md)；环境、HEAD 或依赖改变后重新核对相关事实。

## 交付范围与项目结论

检查起点：分支 `mine`，HEAD `72302e4a8cd5`，工作区与暂存区干净，无 linked worktree。没有修改全局规则、宿主权限、业务源码、资源、依赖、CI、Git 历史或模型子模块；没有 commit 或 push。

项目由启动器配置与资源选择、MaaFramework Pipeline、Python Custom 和公共战斗框架构成。主要风险在任务/选项/Custom 名称契约、语言和平台叠加、缓存完整性、战斗状态清理，以及资源检查和实际设备表现之间的证据边界。具体代码位置和检查路径已写入开发指南，不在此重复维护。

现有基础与处理结果：

| 原有内容与缺口 | 落地处理 |
|---|---|
| 根 AGENTS 已有有效分支、测试、风格、提交规则，但缺少任务路由和验证选择 | 保留有效约束，明确硬约束/默认约定/建议，将细节路由至统一指南 |
| 已有战斗指南可复用，但将 Win32 覆盖绝对化为只需 ROI；站场刷新描述停留在 idle | 根据真实实现修正文案，保留角色开发流程和提示词；增加公共验证入口 |
| 两份 Cursor skill 引用不存在的 `assets/` 源码路径和本地协议全文 | 修为实际源码路径，区分发行包布局；协议语义按依赖版本和 Schema 核对 |
| 中英文 README 链接 `.github/android/README.md`，实际文件不存在 | 改链真实 install 工作流与配方，并加入开发指南入口 |
| 历史 Android 排查包含具体回归与未验设备边界 | 保留原记录，作为历史证据按需引用，不冒充当前真实验收 |
| 缺少当前验证基线及跨会话接手约定 | 本记录保存本轮基线；开发指南提供短交接模板与维护触发条件 |

没有新增 Skill、hook、MCP、配置解析器或脚本包装。现有命令能够覆盖当前静态和模拟验证，新增机制没有已证明的收益。

## 产物与读取时机

| 文件 | 用途 |
|---|---|
| [AGENTS.md](../AGENTS.md) | 接手入口、任务路由、硬约束与按风险工作方式 |
| [开发与验证指南](开发与验证指南.md) | 按任务读取架构、共享契约、命令前提/副作用、检查矩阵、交接与维护 |
| 本记录 | 查看本次实际验证结果和未验证边界；不是常驻规则 |
| [战斗指南](自动战斗框架开发指南.md) | 修改角色与战斗逻辑时读取 |
| [Pipeline skill](../.cursor/skills/maa-punish-pipeline-guide/SKILL.md)、[日志 skill](../.cursor/skills/maa-punish-log-analysis/SKILL.md) | 编辑流水线或分析日志时按链接读取；复用已有内容 |
| [README](../README.md)、[英文 README](../README_en.md) | 人工开发发现入口与真实 Android 构建入口 |

本次 Codex 会话起始上下文提供了既有 AGENTS 内容；更新后的文件经独立 agent 主动读取演练，未验证全新宿主会话自动加载更新后的入口。文件系统中存在 `.cursor/skills/`，但未验证 Cursor 的自动发现/触发，也未验证其他 IDE 自动读取。`docs/` 与上述 skills 的接线是入口链接引导的主动读取；不把文件存在写成所有宿主加载成功。

## 执行基线

复用仓库忽略目录中的 `build-debug/venv/Scripts/python.exe`：Python 3.12.10、pytest 9.1.1、MaaFramework 5.14.2、json-with-comments 1.3.0、jsonschema 4.26.0、referencing 0.37.0。裸 `python` 的 pyenv shim 未配置，因而采用明确解释器路径。原有 Node.js 为 22.22.1，检查器另以局部 Node.js 24.22.0 复核，未修改全局 PATH 或安装。

所有命令在仓库根执行。日志保存在忽略目录 `build-debug/verification-baseline-20261020/`，不作为应提交的产物。实际运行使用以下解释器与进程内环境设置；临时路径已存在，复跑时生成新的 pytest 子目录：

```powershell
$py = (Resolve-Path './build-debug/venv/Scripts/python.exe').Path
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = (Resolve-Path 'build-debug/verification-baseline-20261020/tmp').Path
$env:TMP = $env:TEMP
$testTemp = Join-Path $env:TEMP ('pytest-' + [guid]::NewGuid().ToString('N'))
```

独立复核确认本机未设置 UTF-8 模式时 `sys.stdout.encoding=gbk`，输出 `chr(0x2713)` 会以 `UnicodeEncodeError` 退出 1；设置 `PYTHONUTF8=1` 后同一输出退出 0，编码为 UTF-8、`utf8_mode=1`。指南已加入此前提，避免 Schema 输出符号造成环境失败。Android 工作流使用 Python 3.13 作为构建主机解释器；设备解释器版本由外部 MaaFwApp 的运行时组装决定，本次未验证。

| 状态 | 实际命令或场景 | 结果及证明范围 |
|---|---|---|
| 已运行通过 | `& $py -m pytest -c agent/pytest.ini agent/tests -q -p no:cacheprovider --basetemp $testTemp` | 158 passed，0.99 秒，退出码 0；验证模拟对象下的 agent 行为。独立目录用于规避现有缓存/临时目录权限问题，不跳过测试。 |
| 已运行通过 | `& $py tools/ci/validate_schema.py` | 269 个文件通过，退出码 0；默认范围不含英语资源与 options。 |
| 已运行通过 | `& $py tools/ci/validate_schema.py --resource-dirs resource/base resource/en_US` | 196 个文件通过，退出码 0；包含所选资源与默认接口/任务范围。 |
| 已运行通过 | `& $py tools/ci/validate_schema.py --resource-dirs resource/base resource/pgr_win32 resource/playcover resource/zh_TW resource/ja_JP resource/en_US --task-dirs tasks options` | 308 个文件通过，退出码 0；明确补齐 options 和全部资源目录。 |
| 已运行通过 | `& $py tools/ci/check_resource.py resource/base resource/pgr_win32 resource/playcover resource/zh_TW resource/ja_JP resource/en_US` | 六目录累计加载通过，退出码 0；0 ERR、3 条低显存/软件 GPU 跳过警告。不是实际资源组合的隔离运行。 |
| 已运行通过 | `& $py tools/ci/check_android_pi_profile.py` | 退出码 0，8 条 include 覆盖接口加载期引用，并检查路径存在性；不证明完整资源/agent 载荷、APK 构建、安装或签名。 |
| 已运行通过 | maa-checker 1.2.7，同一 CLI 分别以 Node.js 22.22.1 和 24.22.0 执行 `./interface.json --github=.` | 两次退出码 0，20 组控制器/资源组合，0 error、0 warning；静态检查结果，不是设备运行。 |
| 已运行通过 | 给 `check_resource.py` 传入不存在的资源目录 | 按预期退出码 1，输出路径不存在及 `Resource.Loading.Failed`；验证失败未被吞掉。 |

Node.js 24 检查使用的实际入口：

```powershell
& './build-debug/verification-baseline-20261020/node24/package/bin/node.exe' `
  'build-debug/npm-cache/_npx/e13ed6999c422d59/node_modules/@nekosu/maa-checker/bin/maa-checker' `
  ./interface.json --github=.
```

Node.js 24 来自 npm 的 `node-win-x64@24.22.0` 包，忽略脚本后下载、解压到本地忽略目录。先前通过 `npm exec --package=node@24` 的环境准备尝试因 preinstall/清理权限失败；这是已替代的安装路径失败，不是最终检查失败。

本轮副作用仅在忽略目录中：检查日志、独立临时夹具、npm 缓存/局部 Node.js 包，以及测试追加的 `debug/custom_20261020.log`。原有 `agent/.pytest_cache/` 不可读警告仍存在；本轮未更改 ACL，使用明确的缓存和临时目录选项完成测试。

## 入口检查与接手演练

已完成 8 份变更文档的 UTF-8、无 BOM、末尾换行检查，89 个本地 Markdown 引用全部存在；`git diff --check` 通过。两份修改的 skill frontmatter 和指南显式脚本路径已独立复核。既有文件换行风格保留，新文件为 LF。

独立 agent 从 AGENTS 入口进行只读接手演练，完成两条代表性任务：

| 场景 | 实际定位与验证选择 |
|---|---|
| 小修改：调整“角色缓存更新频率”的显示文案 | 从 `interface.json` 的 import 定位 `options/global_option.json`，再定位 `i18n/zh_cn.json` 的键；选择键引用、JSON/编码/diff 和目标语言显示检查，不扩展到 pytest 或打包。 |
| 跨模块：全局缓存策略修改 | 找到四个选项 attach 节点、`CacheRole.analyze`、`RoleSelection.run` 和共享策略解析；识别来源优先级、自然周/月与自动刷新时点差异、手动刷新/内建名单特例、停止与零结果的写入边界；能选择对应单元测试并区分 Android 配方、ABI 和真机验收。 |

反方检查发现 Android 配方检查的初稿描述超出了脚本能力，已收敛为加载期引用及 include 首段检查；独立环境复核发现 UTF-8 前提遗漏，已用正反向输出实测后补齐。演练证明文档入口与任务路由可用，不代表 IDE 自动发现、代码实现或设备行为已经验收。

## 未验证边界与维护

- `resource/base/model` 当前仅见 `.git` 且子模块 HEAD 无效。资源加载仍可成功，故真实 OCR、神经网络模型推理尚未验证；本任务未自动下载或修改模型。
- 未运行桌面完整打包、Android APK 构建/签名/安装、真实游戏任务、截图 OCR 重放或 PlayCover。文档改动没有这些行为变化，不以重复打包扩大本次范围。
- `CombatRunner` 正常返回成功不等于关卡完成；Schema 缺失输入目录可能只警告跳过；资源脚本累计加载不等于所有运行组合隔离验证。开发指南已明确这些现有工具边界，本次未改变业务语义或测试标准。
- 英语资源与 PlayCover 的产品支持状态，以 README 的未完成/未验说明和后续实际证据为准。
- 后续架构、命令或契约改变时更新相应权威文档及链接。本记录保留原日期与基线，不把后来的结论悄悄写成当时已验证。

回滚范围仅为本次文档：在保留其他人的后续改动后，逐项撤回上述既有文件的本次 diff，移除本次新增指南和记录；不需要回滚运行时、模型、全局设置或数据库。若以后已提交，按 `mine` 分支流程对本次提交做正常撤销，不改写上游 `main`。
