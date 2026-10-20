# iQOO 16 更新渠道与发版契约

## 行为与范围

Android 首页“更新渠道”追加“iQOO 16 适配版”，用户主动选择后，从 `https://nivranic.github.io/MAA_Punish/iqoo16/latest.json` 的静态 JSON 检查新版，从 `nivranic/MAA_Punish` 的 GitHub Releases 下载 APK。手机不携带私密 Token、不请求 GitHub REST API，因而不使用其匿名额度；GitHub Pages 与 APK 资源仍受实际网络可达性影响。固定 MaaFwApp `bc55dac4c8231ab73e7ee42bd38c43e322838b14` 由本仓 [补丁入口](../../.github/android/patch_maafwapp.py)、[精确锚点](../../.github/android/maafwapp_patches.py) 和小型 Kotlin overlay 适配；不把外部 checkout 当成本仓源码，也不复制整份上游文件。

保留 `STABLE`、`BETA` 的枚举名、默认值、MirrorChyan/GitHub 行为和持久化设置。新增渠道保存为 `IQOO16`；选择期间有效更新源显示 GitHub，禁用源切换并隐藏无关 CDK；离开该渠道恢复原持久化源。`UpdateService` 为该渠道清除 MirrorChyan RID/CDK，通过现有 DI 单独路由到 `Iqoo16StaticUpdateClient`，复用 `HttpClientHelper`、更新结果类型、下载器与系统安装器。静态请求失败直接返回原有结构化错误，配置缺失同样失败，均不向 REST API 或 MirrorChyan 回退；原 GitHub/MirrorChyan 客户端和其他渠道路由不变。切换渠道或源清除旧检查结果；检查、下载期间锁定选择，防止用旧源结果触发新渠道下载。长渠道文案使用 FlowRow 换行。

## 版本与 APK 选择

`mine` 使用 [版本工具](../../tools/ci/mine_apk_version.py)：从 `HEAD` 可达、严格匹配 `vX.Y.Z` 的正式标签中按数值取最高版本，patch 加一，再追加 `-iqoo.<git提交数>`。例如最高正式标签 `v3.12.5`、提交数 `2185` 时：

| 字段 | 示例 |
|---|---|
| Release tag | `mine-v3.12.6-iqoo.2185` |
| APK `versionName` | `3.12.6-iqoo.2185` |
| APK `versionCode` | `2185` |
| APK 文件 | `MPA-android-v3.12.6-iqoo.2185.apk` |

缺少可达正式标签时失败，不能回落到猜测版本。Android 与 meta 两个 job 在 `mine` 的完整 checkout 后，都显式从官方 `https://github.com/overflow65537/MAA_Punish.git` 获取 `v*` 标签，再由版本工具筛选 `HEAD` 可达的最高正式版本；fork 可能没有同步官方标签，`fetch-depth: 0` 本身不能补足该基线。获取使用 `git fetch --no-tags <官方地址> 'refs/tags/v*:refs/tags/v*'`，不强制覆盖，标签冲突直接失败。两个 job 采用相同来源和筛选规则，保证生成版本与 APK 验收基线一致。只有 `mine` 的版本生成和 `BUILD_VERSION_NAME` 改用上述契约，其他分支及正式 `v*` 标签沿用现有流程。

新增渠道仅接受 `mine-vX.Y.Z-iqoo.<正整数>`，按已有 SemVer 库比较，不手写客户端版本排序；同时要求清单版本码大于实际 `BuildConfig.VERSION_CODE`，检查及下载解析均拒绝相同版本和降级。发布的是双 ABI universal APK，清单只允许该 tag 同名的 release APK，不能指向 debug APK、其他仓库、外链、带 Token 的查询 URL 或空摘要。下载继续使用原 SHA-256 校验与系统安装器。

## 静态清单 v1 与 Pages 发布

清单由 [iqoo16_manifest.py](../../tools/ci/iqoo16_manifest.py) 从 GitHub 已发布资产元数据生成，不在源码中维护手填版本：

```json
{
  "schema_version": 1,
  "channel": "IQOO16",
  "application_id": "com.overflow65537.maafw.punish",
  "version_name": "3.12.6-iqoo.2190",
  "version_code": 2190,
  "release_tag": "mine-v3.12.6-iqoo.2190",
  "apk_url": "https://github.com/nivranic/MAA_Punish/releases/download/mine-v3.12.6-iqoo.2190/MPA-android-v3.12.6-iqoo.2190.apk",
  "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "release_url": "https://github.com/nivranic/MAA_Punish/releases/tag/mine-v3.12.6-iqoo.2190"
}
```

示例摘要仅说明格式，不对应实际 APK。客户端校验 JSON 字段类型、协议/渠道/包名、严格 tag、版名、正整数版本码 N 三者一致、精确 HTTPS 下载与 Release URL、64 位小写十六进制摘要；协议、JSON、HTTP、网络失败映射到现有错误，协程取消继续向上传播。既有检查/下载动作每次各发一次请求，`Cache-Control: no-cache` 和分钟级查询键减轻边缘旧缓存，不新增轮询。

`install.yml` 的 `iqoo16_pages` 只在本 fork `mine` 的 push 或手动执行且 `mine_apk_release` 成功后运行。它通过 `GH_TOKEN: ${{ github.token }}` 与 `gh api --paginate --slurp` 认证读取完整预发布列表；生成清单前要求本次 tag 存在唯一精确 APK、`state=uploaded`、正大小与有效 `sha256:` digest。官方标签、历史 SHA 格式和不完整的其他 Release 不参与候选。随后按数值 SemVer 选择最高有效发布，并要求其版本码不低于所有有效候选；这与严格限定的 `X.Y.Z-iqoo.N` 顺序一致。CI 发版前原有 APK 实物包名/版本/证书门禁继续保留。

为避免晚到或重跑覆盖：同 tag 的 APK 发布 job 使用独立 concurrency 且不取消进行中的发布；[softprops v3 官方 `overwrite_files: false`](https://github.com/softprops/action-gh-release#inputs) 保持已成功发布的同名资产字节不变，重跑的本地重建包不会替换它。Pages 使用另一个固定 concurrency 串行覆盖“收集—生成—部署”全过程，每次重新选择最高已发布版本，而不只看当前分支 HEAD（纯文档提交不应使有效构建失效）。部署前读取上一清单，拒绝降低 SemVer/版本码，拒绝同 tag 的摘要/URL 变化；初次站点 404 允许建立清单，其他读取/解析失败停止部署。出现缺少 digest、残缺资产或清单不一致时失败保留上次站点，不自动替换已发布同版资产。若确需修复已发布包，应提交一个版本码递增的新版本。

Pages 只包含公开 JSON，不包含 APK、Token 或密钥。按 [GitHub 官方自定义 Pages 工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)，部署使用 `actions/configure-pages@v5`、`actions/upload-pages-artifact@v4`、`actions/deploy-pages@v4`，job 权限仅 `contents: read`、`pages: write`、`id-token: write`，环境名 `github-pages`。仓库 Settings → Pages 的 Source 必须为 GitHub Actions；Settings → Environments → github-pages 的部署分支规则必须允许 `mine`。环境审批/分支限制仍由仓库设置执行，不能仅凭 Source 切换推断已部署成功。

## 首包迁移与签名

历史 `mine-v0.0.0-ci.<日期>-<SHA>` 与 APK 实际版本不一致，不能安全参与比较。已发布 `cb3acf9c` APK 实物 `versionName=cb3acf9c`、`versionCode=2184`，不是可比较的 SemVer；新增渠道不会把它当作版本零。旧包也没有新增渠道，因此首次需要手动迁移到新包。

后续 Release APK 使用专用长期签名，期望证书 SHA-256 为 `a4eb8265896986293c8a746324c9ed25a79af0aee81e3ef77f249f31037db9a6`。本 fork 的 `mine` 构建必须同时提供四项 `ANDROID_*` 签名 Secrets，否则失败，避免回落随机 debug 签名并误发布到升级渠道；其他分支与 fork 的既有开发签名回退保持原样。旧临时 debug 签名不同，首次迁移可能必须卸载旧包；卸载前保存需要保留的配置，不能承诺跨签名覆盖安装。私钥及密码不入库。正常升级要求包名、签名一致并且 `versionCode` 增大；重写分支历史可能破坏提交数单调性，需单独审查，不能用本方案绕过 Android 降级限制。

已使用专用签名的 `3.12.6-iqoo.2189` 客户端仍读取 Releases API。它可通过旧渠道检测（若 API 可达）或手动下载一个更高版本码、同证书的新 APK 覆盖升级；切换到本次静态客户端只需这一次正常同签名升级，不因查询协议改变而需要卸载。手机中保留旧 APK 不会因开启 Pages 自动改变查询逻辑。

## 验证与验收

- Python 3.12：`python -m unittest discover -s .github/android/tests -v`，保留原 12 项基础回归，覆盖幂等、重复/漂移锚点、overlay 冲突时不部分写入，以及版本生成、APK metadata、实物 badging/证书；新增清单生成与类型、上传状态、摘要、精确资产、并发旧 run、同版不变和防降级回归。
- 固定上游真实源码运行补丁两次，确认首次应用、二次无改动；升级固定 SHA 时重新审查锚点与共同消费者。
- Android CI 在实际签名 variant 下执行 `:app:testReleaseUnitTest` 或 `:app:testDebugUnitTest`，选择 `com.aliothmoon.maafw.update.*`、原 `SettingsViewModelTest` 与新增 `Iqoo16SettingsViewModelTest`。固定 AGP 9.2.1 默认只为 `testBuildType=debug` 启用单元测试，CI 在该次 Gradle 测试命令显式传入受支持的 `-Pandroid.onlyEnableUnitTestForTheTestedBuildType=false` 来启用所选 variant，不修改上游全局配置。包含真实客户端静态路由、合法/畸形清单、HTTP/网络/取消、无 REST 回退、无 CDK 下载、原渠道保留、旧 hash 拒绝、SemVer/Android 版本码防降级与旧结果清理。
- `mine` 构建后检查 Gradle `output-metadata.json` 和 APK 实物 `aapt dump badging` 的包名、版本名、版本码，并由 `apksigner verify --print-certs` 检查专用证书。构建和 CI 测试失败时不发布该 APK。
- 发布后验证线上 Pages JSON 和对应真实 APK，再用真实客户端验证低版发现、同版/高版不误报和下载解析；静态渠道不再需要匿名 API 验收。本机开发/API 验收若需要认证，凭据仅从用户授权的安全存储加载，不写入代码或 APK，CI 使用内置 Token。真实手机上的首页展示、网络访问、系统安装确认和保留配置升级需设备验收；源码/Mock、构建、静态证书一致不能代替设备升级成功。
