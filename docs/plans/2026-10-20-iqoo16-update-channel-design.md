# iQOO 16 更新渠道与发版契约

## 行为与范围

Android 首页“更新渠道”追加“iQOO 16 适配版”，用户主动选择后，从 `nivranic/MAA_Punish` 的 GitHub Releases 检查和下载 APK。固定 MaaFwApp `bc55dac4c8231ab73e7ee42bd38c43e322838b14` 由本仓 [补丁入口](../../.github/android/patch_maafwapp.py)、[精确锚点](../../.github/android/maafwapp_patches.py) 和小型 Kotlin overlay 适配；不把外部 checkout 当成本仓源码，也不复制整份上游文件。

保留 `STABLE`、`BETA` 的枚举名、默认值、MirrorChyan/GitHub 行为和持久化设置。新增渠道保存为 `IQOO16`；选择期间有效更新源显示 GitHub，禁用源切换并隐藏无关 CDK；离开该渠道恢复原持久化源。所有检查与解析均在 `UpdateService` 统一覆盖仓库和源，并清除 MirrorChyan RID/CDK；GitHub 客户端对该渠道显式跳过 MirrorChyan 定版。切换渠道或源清除旧检查结果；检查、下载期间锁定选择，防止用旧源结果触发新渠道下载。长渠道文案使用 FlowRow 换行。

## 版本与 APK 选择

`mine` 使用 [版本工具](../../tools/ci/mine_apk_version.py)：从 `HEAD` 可达、严格匹配 `vX.Y.Z` 的正式标签中按数值取最高版本，patch 加一，再追加 `-iqoo.<git提交数>`。例如最高正式标签 `v3.12.5`、提交数 `2185` 时：

| 字段 | 示例 |
|---|---|
| Release tag | `mine-v3.12.6-iqoo.2185` |
| APK `versionName` | `3.12.6-iqoo.2185` |
| APK `versionCode` | `2185` |
| APK 文件 | `MPA-android-v3.12.6-iqoo.2185.apk` |

缺少可达正式标签时失败，不能回落到猜测版本。Android 与 meta 两个 job 在 `mine` 的完整 checkout 后，都显式从官方 `https://github.com/overflow65537/MAA_Punish.git` 获取 `v*` 标签，再由版本工具筛选 `HEAD` 可达的最高正式版本；fork 可能没有同步官方标签，`fetch-depth: 0` 本身不能补足该基线。获取使用 `git fetch --no-tags <官方地址> 'refs/tags/v*:refs/tags/v*'`，不强制覆盖，标签冲突直接失败。两个 job 采用相同来源和筛选规则，保证生成版本与 APK 验收基线一致。只有 `mine` 的版本生成和 `BUILD_VERSION_NAME` 改用上述契约，其他分支及正式 `v*` 标签沿用现有流程。

新增渠道仅接受 `mine-vX.Y.Z-iqoo.<正整数>`，按 SemVer 数值比较，选择该 tag 同名 Android APK，复用原 ABI、摘要校验、下载与系统安装器。无 APK、错误文件名、同名 debug/release 多候选均不误选。检查列表包含预发布版本，不依赖 GitHub `/releases/latest`。

## 首包迁移与签名

历史 `mine-v0.0.0-ci.<日期>-<SHA>` 与 APK 实际版本不一致，不能安全参与比较。已发布 `cb3acf9c` APK 实物 `versionName=cb3acf9c`、`versionCode=2184`，不是可比较的 SemVer；新增渠道不会把它当作版本零。旧包也没有新增渠道，因此首次需要手动迁移到新包。

后续 Release APK 使用专用长期签名，期望证书 SHA-256 为 `a4eb8265896986293c8a746324c9ed25a79af0aee81e3ef77f249f31037db9a6`。本 fork 的 `mine` 构建必须同时提供四项 `ANDROID_*` 签名 Secrets，否则失败，避免回落随机 debug 签名并误发布到升级渠道；其他分支与 fork 的既有开发签名回退保持原样。旧临时 debug 签名不同，首次迁移可能必须卸载旧包；卸载前保存需要保留的配置，不能承诺跨签名覆盖安装。私钥及密码不入库。正常升级要求包名、签名一致并且 `versionCode` 增大；重写分支历史可能破坏提交数单调性，需单独审查，不能用本方案绕过 Android 降级限制。

## 验证与验收

- Python 3.12：`python -m unittest discover -s .github/android/tests -v`，覆盖幂等、重复/漂移锚点、overlay 冲突时不部分写入，以及版本生成、APK metadata 和实物 badging 校验。
- 固定上游真实源码运行补丁两次，确认首次应用、二次无改动；升级固定 SHA 时重新审查锚点与共同消费者。
- Android CI 在实际签名 variant 下执行 `:app:testReleaseUnitTest` 或 `:app:testDebugUnitTest`，选择 `com.aliothmoon.maafw.update.*`、原 `SettingsViewModelTest` 与新增 `Iqoo16SettingsViewModelTest`。包含新渠道路由/无 CDK 下载、原渠道保留、旧 hash 拒绝、版本比较、不降级、资产选择与旧结果清理。
- `mine` 构建后检查 Gradle `output-metadata.json` 和 APK 实物 `aapt dump badging` 的包名、版本名、版本码，并由 `apksigner verify --print-certs` 检查专用证书。构建和 CI 测试失败时不发布该 APK。
- 新渠道首包发布后，用真实 Release API 与 APK 验证检查/下载选择；真实手机上的首页展示、网络访问、系统安装确认和保留配置升级需设备验收。源码/Mock、构建、静态证书一致不能代替设备升级成功。
