# Repository Guidelines

## 项目结构与模块划分

MAA_Punish 基于 MaaFramework，通过图像识别与模拟输入实现《战双帕弥什》自动化。

- `agent/`：Python 动作、识别与战斗逻辑；`agent_file.py` 注册处理器，`action/combat/roles/` 存放角色实现。
- `resource/base/`：公共流水线、图像及 `model/` Git 子模块。平台与语言覆盖资源位于 `pgr_win32/`、`playcover/`、`zh_TW/`、`ja_JP/`、`en_US/`。
- `interface.json`、`tasks/`、`options/`、`i18n/`：分别定义启动器配置、任务、选项与翻译。
- `agent/tests/`、`agent/test_support/`：单元测试与模拟对象。`tools/ci/` 存放打包和检查脚本，`tools/schema/` 存放 Schema，`docs/` 提供战斗开发文档。

## 构建、测试与开发命令

使用与 agent CI 一致的 Python 3.12；Windows 下使用 PowerShell 7（`pwsh`）。以下命令均在仓库根目录执行：

- `git submodule update --init --recursive`：获取模型资源。
- `python -m pip install -r requirements-dev.txt`：安装运行依赖与 pytest。
- `python -m pytest -c agent/pytest.ini agent/tests -q`：运行 agent 单元测试。
- `npx @nekosu/maa-checker@latest ./interface.json`：检查资源引用；CI 使用 Node.js 24。
- `python tools/ci/check_resource.py resource/base resource/pgr_win32 resource/playcover resource/zh_TW resource/ja_JP resource/en_US`：检查资源加载。
- 先执行 `python -m pip install jsonschema==4.26.0 referencing==0.37.0`，再执行 `python tools/ci/validate_schema.py`：校验 Schema。检查英文流水线时添加 `--resource-dirs resource/base resource/en_US`，默认范围不包含英文覆盖资源。
- `python tools/ci/install.py`：将资源组装到 `install/`。启动器需单独提供，完整打包流程见 `.github/workflows/install.yml`。

本地游戏验证使用 [MFW-PyQt6](https://github.com/overflow65537/MFW-PyQt6/releases) 加载 `interface.json`，由启动器启动 agent。

## 编码风格与命名约定

使用四空格缩进，遵循相邻 Python/JSON 代码的风格。文件采用 UTF-8 编码并保留末尾换行；JSON/JSONC 不得带 BOM。VS Code 配置使用内置 JSON 格式化器。Python 函数和新增战斗模块使用 `snake_case`，类名使用 `PascalCase`。保持流水线标识符与本地化键在各处引用一致。

## 测试规范

pytest 测试放在 `agent/tests/`，文件命名为 `test_*.py`，复用 `agent/test_support/` 中的模拟对象。行为变更应补充针对性的回归测试，目前未配置覆盖率数值门槛。资源变更需执行适用的资源与 Schema 检查，并在受影响的平台、语言版本中进行游戏验证；单元测试不能证明真实设备行为正确。

## 提交与 Pull Request 规范

历史提交使用 `fix:`、`feat:`、`chore:` 等 Conventional Commit 前缀，部分带有语言范围。优先使用简洁的中文摘要，历史中也有英文提交。说明实际行为变化，例如 `fix: 修复战斗结束误触发`。

PR 应说明改动、关联相关 Issue、列出受影响的平台和语言版本，并记录验证命令与结果。识别或界面变更应附截图或脱敏日志。不要提交生成的安装包、缓存、凭据或签名密钥。
