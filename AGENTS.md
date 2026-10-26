# Repository Guidelines

MAA_Punish 通过 MaaFramework 的图像识别、Pipeline 与 Python 自定义处理器，自动执行《战双帕弥什》任务。源码从根目录的 `interface.json`、`tasks/`、`options/`、`resource/` 和 `agent/` 开始；不要把发行包中的 `assets/` 布局当成源码路径。

## 接手入口与按需阅读

先确认目标、完成条件以及 Git 分支和工作区现状，再按任务读取下表。架构、命令与验证选择统一维护在 [开发与验证指南](docs/开发与验证指南.md)，无需每次通读全部文档。

| 任务 | 首先定位 | 按需资料 |
|---|---|---|
| 新增任务、选项、翻译或修复识别 | `interface.json` → `tasks/` / `options/` → `resource/**/pipeline/`；联动 `i18n/` | [开发指南](docs/开发与验证指南.md)、[Pipeline 指南](.cursor/skills/maa-punish-pipeline-guide/SKILL.md) |
| Python Custom 动作或识别 | `agent/agent_file.py` 的注册名 → `agent/action/` / `agent/recognition/` | 开发指南中的运行链路、契约与测试矩阵 |
| 角色打法、切人、战斗状态 | `agent/action/combat/entry/runner.py` → 同模块的 `core/`、`actions/`、`roles/` | [自动战斗框架开发指南](docs/自动战斗框架开发指南.md) |
| 角色扫描、缓存、按钮布局 | `agent/action/basics/RoleSelection.py`、`agent/action/basics/role_cache_policy.py`、`agent/action/basics/InterfaceZone/` | 开发指南中的共享状态与设备边界 |
| 卡死、超时、识别失败 | 先串联同一次运行的框架、界面和 agent 日志，再找对应节点 | [日志分析指南](.cursor/skills/maa-punish-log-analysis/SKILL.md)、[已有 Android 排查记录](docs/Android识别与战斗异常排查-20261020.md)（历史证据） |
| CI、打包、Android 适配 | `.github/workflows/`、`tools/ci/`、`.github/android/` | 开发指南中的安装、构建与验证边界 |

加载边界：本文件是仓库的项目指令入口；宿主是否自动注入，以该工具实际行为为准。`docs/` 与 `.cursor/skills/` 由 agent 按链接主动读取；文件存在不代表所有 IDE 自动发现或执行。它们不能覆盖宿主指令、有效用户要求或权限限制。

## 硬性约束

- **Fork 边界**（既有项目要求）：上游为 `https://github.com/overflow65537/MAA_Punish`，主分支是 `main`。`main` 能且只能同步上游 `main`，不得提交个人开发或修复；个人需求与修复只在 `mine`。同步顺序为更新 `main`、合并到 `mine`、按授权推送 `mine` 到 fork。
- **项目真机设备策略**：本项目所有 agent 跨宿主、跨对话处理目标真机任务及设备空闲时，必须执行[项目级真机设备策略](docs/开发与验证指南.md#项目级真机设备策略)：防自动锁屏、实际最低亮度、优先现成Manual黑罩；新scope／重连复核失效即正常重设，不随final／case／lease结束还原。仅用户明确结束该设备调试或整体目标收尾恢复原值；纯离线无设备不阻塞。
- **保留工作区**：切换或合并前检查分支、工作树和 staged 变更，保留用户及其他 agent 的改动；推送前审查待推送提交。普通开发请求不代表已授权 commit 或 push。
- **接口与资源契约**：任务入口、选项覆盖目标、Pipeline 节点名、Python Custom 注册名、角色 `cls_name` 和本地化键是实际消费者之间的接口。修改时核查引用与共同消费者，详见开发指南的契约表；不能只改一个定义便宣称完成。
- **验证真实性**（既有项目规范）：行为变更补充针对性的回归测试；资源变更执行适用的资源与 Schema 检查，并安排受影响平台、语言的游戏验证。未能运行的检查和设备验收必须明确标记，单元测试、资源加载及打包均不能替代真实游戏结果。
- **交付边界**：不要提交生成的安装包、缓存、凭据或签名密钥。日志、截图和配置摘录先脱敏。
- **文件编码**（既有工程规范）：文件使用 UTF-8 并保留末尾换行；JSON/JSONC 不得带 BOM。
- Windows 命令使用 PowerShell 7（`pwsh`），不得使用 Windows PowerShell 5.1；agent 开发与单元测试使用 CI 对齐的 Python 3.12。环境安装与完整命令见开发指南。

## 默认约定与建议

- 默认沿用四空格缩进；JSON 格式化遵循 `.vscode/settings.json` 的内置格式化器；Python 函数和新增战斗模块使用 `snake_case`，类名使用 `PascalCase`。不要为统一风格顺带格式化无关文件。
- 默认复用 `agent/test_support/` 的模拟对象；pytest 文件放在 `agent/tests/test_*.py`。当前没有覆盖率数值门槛，不据此省略有实际行为风险的回归验证。
- 小修改只读取相关上下文、实施和执行必要检查；常规功能跟踪相关链路；公共能力、缓存、跨平台资源及打包变更再扩展共同消费者和兼容性检查。复杂任务建议先写简短计划，短任务无需创建长期计划文件。
- 建议先复用现有处理器、测试和脚本；若需要新增执行机制，先证明现有入口无法满足需求，并说明消费者、验证方式与维护成本。

## 交付与维护

提交摘要沿用 Conventional Commit（如 `fix:`、`feat:`、`chore:`，可带实际需要的 scope），优先简洁中文，说明行为变化，例如 `fix: 修复战斗结束误触发`。

交付或 PR 说明改动、关联 Issue（如有）、影响的平台与语言、实际执行的命令与结果、未验证边界。识别或界面变更附截图或脱敏日志。跨会话任务按开发指南的交接模板留下目标、文件、状态、验证和下一步；无需把每次过程写入永久规范。

架构、目录归属、公共契约或运行命令改变时，更新对应的权威文档及入口链接；不要求每次代码修改都改文档。一次性验收与技术债记录只作带日期的证据，不能未经核实升级为硬规则。
