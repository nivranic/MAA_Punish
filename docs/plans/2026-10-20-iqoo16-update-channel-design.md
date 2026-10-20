# iQOO 16 更新渠道与发版契约

## 包身份、共存与数据

本 fork 的 `mine` Android APK 是独立共存版：

| 项目 | 官方 / 历史同包名版 | iQOO 专版 |
|---|---|---|
| applicationId | `com.overflow65537.maafw.punish` | `com.overflow65537.maafw.punish.iqoo16` |
| 桌面名称 | `MAA Punish` | `MAA Punish iqoo16` |
| APK 精确名称 | `MPA-android-vX.Y.Z-iqoo.N.apk`（历史 fork） | `MPA-android-iqoo16-vX.Y.Z-iqoo.N.apk` |
| 静态清单 | `/MAA_Punish/iqoo16/latest.json`（历史 fork） | `/MAA_Punish/iqoo16/standalone/latest.json` |
| 可选渠道 | 原有行为 | 仅“iQOO 16 适配版” |

官方签名与本 fork 的专用长期证书不同，不能保留全部数据直接互相覆盖。安装独立版不卸载、不清理或覆盖官方版；官方原数据保留。两版运行配置、定时、缓存和设备权限各自独立，不自动共享。通知、电池白名单、Shizuku/root、无障碍等权限需为新包单独授予；两版不应同时执行游戏任务。

受支持的任务/定时/应用设置可沿用 MaaFwApp 原有导出/导入迁移。`ConfigBackupService` 将 applicationId 作为 `BackupAppInfo` 来源信息记录；`ConfigImport` 按 `backup.project.name == definition.name` 校验备份，MXU 文件与分享码同样保留原项目校验。配方的 app.id/label 不改 `interface.json` 的项目名，因此无需放宽来源规则。凭据、设备授权及全部私有数据不承诺迁移，也不通过提取官方私有目录完成迁移。

## 固定上游与最小适配

固定 MaaFwApp `bc55dac4c8231ab73e7ee42bd38c43e322838b14` 由本仓 [补丁入口](../../.github/android/patch_maafwapp.py)、[精确锚点](../../.github/android/maafwapp_patches.py) 和 Kotlin overlay 适配；不把外部 checkout 当成本仓源码，不复制整份上游文件。全部目标先预检再写入，锚点漂移、重复或 overlay 冲突失败，重复应用允许无改动。

MaaFwApp 的 `AndroidApplicationConventionPlugin` 原生接受点分后缀，`APP_ID_PATTERN` 为 `[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*`；`BuildProfile.expandEnv` 原生支持 `${NAME:-fallback}`。[配方](../../.github/android/pi-profile.yaml) 使用这两项已有能力，由 CI 仅为 `nivranic/MAA_Punish` 的 `mine` 设置 `MAA_PUNISH_APP_ID=punish.iqoo16`、`MAA_PUNISH_APP_LABEL=MAA Punish iqoo16`。其他分支/正式标签及其他 fork 沿用 `punish` / `MAA Punish`，不改变 namespace 或 Kotlin/Java 类包名。

固定上游已按安装身份派生共存边界：FileProvider、ShizukuProvider、root bootstrap 的 authority 使用 `${applicationId}`；系统安装器/日志分享使用 `context.packageName`；定时触发和执行 action 使用 `BuildConfig.APPLICATION_ID`，显式组件及 PendingIntent 由本包创建；提权子进程、Sui 初始化和 bootstrap 使用当前包名/UID；运行数据用应用私有目录，更新缓存用 `context.cacheDir`。主 Manifest 没有自定义全局 deep link、共享 UID 或固定包名 authority。快捷方式、无障碍与通知仍由 Android 按安装包隔离。以上是固定源码核查，真实双包安装和权限行为仍需设备验收。

## 专版单渠道策略

专版首页只显示“iQOO 16 适配版”和固定 GitHub 源，不显示正式版、公测版、MirrorChyan 或 CDK。`UpdateChannelPolicy` 在生产 DI 中依据 applicationId 启用；通用组件构造默认保持原渠道语义，便于保留上游回归。

策略同时覆盖 AppSettings 默认值、每次 DataStore emission（包括恢复/导入）、写入口、便携导入及导出快照；专版将 `STABLE` / `BETA` / 无效旧值归一化为 `IQOO16`，更新源为 `GITHUB`，CDK 为空。SettingsViewModel 的首帧、有效 UI 状态、选择 intent、启动自检、手动检查、下载解析同样应用策略。等待异步持久化回读时等待归一化后的目标值，避免仍等被拒绝的 `STABLE` 而卡住。切换渠道或源清除旧结果；检查、下载期间锁定选择。

`UpdateService` 再次归一化 check/resolve 请求，清除官方 RID/CDK，固定 fork 后路由 `Iqoo16StaticUpdateClient`。即使旧配置绕过 UI 或直接调用服务，也不能请求官方 REST/MirrorChyan 或解析官方 APK。静态请求/配置失败直接返回原有结构化错误，取消继续传播，不向其他客户端回退。手机下载仍复用 `HttpClientHelper`、原下载摘要验证及系统安装器，不携带私密 Token，不使用 GitHub REST API 匿名额度；Pages 和 Release 资产仍受网络可达性影响。

## 版本与签名

`mine` 使用 [版本工具](../../tools/ci/mine_apk_version.py)：从 `HEAD` 可达且严格匹配 `vX.Y.Z` 的正式标签中按数值取最高版本，patch 加一，再追加 `-iqoo.<git提交数>`。例如最高正式标签 `v3.12.5`、提交数 `2195`：

| 字段 | 示例 |
|---|---|
| Release tag | `mine-v3.12.6-iqoo.2195` |
| APK versionName | `3.12.6-iqoo.2195` |
| APK versionCode | `2195` |
| 专版 APK | `MPA-android-iqoo16-v3.12.6-iqoo.2195.apk` |

缺少可达正式基线时失败，不猜测版本。fork 不一定同步官方标签，Android 与 meta 两个 job 在 `mine` 完整 checkout 后都执行 `git fetch --no-tags https://github.com/overflow65537/MAA_Punish.git 'refs/tags/v*:refs/tags/v*'`；不强制覆盖，冲突失败。两个 job 用相同规则，`BUILD_VERSION_NAME` 显式为去 `v` 的版名，版本码仍为提交数；同提交重跑版本不变。正式 `v*` 标签沿用原发布链。

客户端只接受 `mine-vX.Y.Z-iqoo.<正整数>`，使用既有 SemVer 库，同时要求清单 versionCode 大于已安装 `BuildConfig.VERSION_CODE`；检查/解析均拒绝相同版本和降级。发布双 ABI universal release APK；不接收 debug、外仓、外链、带 Token 查询串或空摘要。

长期证书 SHA-256 保持 `a4eb8265896986293c8a746324c9ed25a79af0aee81e3ef77f249f31037db9a6`，不为独立包重新生成密钥。本 fork 的 `mine` 必须提供全部四项 `ANDROID_*` 签名 Secrets，否则失败，不回落随机 debug。其他构建的既有开发签名回退保持原样。CI 对 metadata 及 APK 实物 `aapt dump badging` 校验包名、versionName、versionCode、应用名称，对 `apksigner verify --print-certs` 的全部证书摘要校验专用证书；未通过不发布。

正常专版覆盖升级要求新包名、证书保持一致且版本码递增；重写分支历史可能破坏提交数单调性，必须另行审查。历史 `cb3acf9c` / 临时 debug 包、旧同包名 `2189` / `2190` 均不自动迁移至新包名。首次单独安装专版，官方版及历史原包按需保留；不能通过卸载官方来完成迁移。

## 双静态清单 v1

[清单工具](../../tools/ci/iqoo16_manifest.py) 从认证读取的已发布 Release 资产元数据生成，不在源码手填发布版本。新地址为 `https://nivranic.github.io/MAA_Punish/iqoo16/standalone/latest.json`：

```json
{
  "schema_version": 1,
  "channel": "IQOO16",
  "application_id": "com.overflow65537.maafw.punish.iqoo16",
  "version_name": "3.12.6-iqoo.2195",
  "version_code": 2195,
  "release_tag": "mine-v3.12.6-iqoo.2195",
  "apk_url": "https://github.com/nivranic/MAA_Punish/releases/download/mine-v3.12.6-iqoo.2195/MPA-android-iqoo16-v3.12.6-iqoo.2195.apk",
  "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "release_url": "https://github.com/nivranic/MAA_Punish/releases/tag/mine-v3.12.6-iqoo.2195"
}
```

示例摘要不对应真实包。JSON 字段类型、协议/渠道/包名、严格 tag、版名/正整数版本码 N 三方一致、精确 HTTPS fork APK/Release URL、64 位小写十六进制摘要都需校验。检查和下载解析每次各请求一次，`Cache-Control: no-cache` 和分钟查询键减轻旧边缘缓存，不新增轮询。

历史地址 `https://nivranic.github.io/MAA_Punish/iqoo16/latest.json` 保留旧 `application_id=com.overflow65537.maafw.punish` 与精确 `MPA-android-v*.apk` 规则，由 `--feed legacy` 从合法历史已发布资产重新生成并比对已部署清单；新文件名不能成为旧 feed 候选。旧 `3.12.6-iqoo.2190` 因而仍接收原包清单，不收到新 package。旧 `2189` 及以前实际仍按其原 Releases API 客户端运行，Pages 不能改变已安装代码。

新 feed 使用 `--feed standalone --required-tag <本次tag>`，必须找到本次完整新身份 APK；其他历史包、正式标签和 SHA/debug 发布不参与候选。每条 feed 都要求唯一精确资产、`state=uploaded`、正大小、有效 GitHub `sha256:` digest，并按数值 SemVer 选择最高合法 Release；同版本码不得被不同 tag 复用，最高 SemVer 的 code 不能落后于其他有效候选。上一次清单必须匹配本 feed 的包名/资产类型；拒绝 SemVer/code 降级，拒绝同 tag 的任何元数据变化或跨 feed 替换。缺少合法历史包也失败，不能悄悄丢弃旧 feed。

## 发布、并发与权限

`install.yml` 的专版发布成功后才运行 Pages job，且限于本 fork `mine` 的 push/手动执行。APK 发布仅依赖 Android；桌面 job 的状态不能当作 Android 安装结果。发布使用按 tag 的 concurrency、不取消进行中的发布、[softprops v3 `overwrite_files: false`](https://github.com/softprops/action-gh-release#inputs)，同提交重跑复用已上传字节，避免重建覆盖资产破坏线上摘要。修复已发布包应提交一个更高版本码的新版本。

Pages 以 `GH_TOKEN: ${{ github.token }}` 与 `gh api --paginate --slurp` 认证读取完整 Release 列表，一次快照分别生成两个 feed。部署固定 concurrency 覆盖“收集—生成—部署”全过程；旧 run 晚到时重新选最高合法发布，不回退到自身版本。分别读取两个上一清单，首次 HTTP 404 允许建立；其他网络/HTTP/解析失败停止部署，任一 feed 失败都保留上一整个站点。artifact 同时包含 `iqoo16/latest.json` 和 `iqoo16/standalone/latest.json`，只有公开 JSON，没有 APK、Token 或密钥。

依 [GitHub 官方 Pages 工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)，使用 `actions/configure-pages@v5`、`actions/upload-pages-artifact@v4`、`actions/deploy-pages@v4`。Pages job 权限仅 `contents: read`、`pages: write`、`id-token: write`，环境 `github-pages`；仓库 Source 需选 GitHub Actions，环境部署规则需允许 `mine`。仅设置 Source 不等于实际部署成功。CI 使用内置 Token；本机禁止将共享私密 Token 打包进应用。

## 验证与验收边界

- Python 3.12：`python -m unittest discover -s .github/android/tests -v`。覆盖全部原补丁/版本/签名回归，并覆盖新身份/label、双 feed 精确资产隔离、旧 feed 保留、类型与摘要、同版不可变、防降级、版本码不复用、CLI 产物仅两份 JSON。
- 固定真实上游源码应用两次补丁，确认首轮成功、次轮零改动。升级固定 SHA 时重新核对 anchors 与共存消费者。
- Android CI 对实际签名 variant 执行 `:app:testReleaseUnitTest` 或 `:app:testDebugUnitTest`，选择更新器全部 tests、原 `SettingsViewModelTest` 与 `Iqoo16SettingsViewModelTest`。AGP 9.2.1 默认只为 debug 启用单元测试，当前命令显式传 `-Pandroid.onlyEnableUnitTestForTheTestedBuildType=false`，不改上游全局配置。新增专版默认/导入归一化、意图不能激活旧渠道、首帧/启动/手动/下载请求一致、服务强制路由及失败不回退；保留通用渠道原回归。
- `actionlint .github/workflows/install.yml`、Android 配方检查和 diff 空白检查。构建后对 metadata、APK 实物身份/label、全部签名证书执行门禁；这些验证不能代替运行时验收。
- 发布后验证两个线上 JSON 及新 APK 完整摘要、实际包名/label、证书和版本码；使用真实 Kotlin 客户端验证低版发现、同版/高版不误报、下载解析及拒绝旧身份清单。
- 真机安装前后核对官方包仍存在且 UID/签名/数据目录未被替换；仅安装独立包并核对双包和专版首页。不声称 ADB 包信息能证明全部用户数据逐项完好；需实际观察配置迁移、授权、游戏执行及未来覆盖升级时，分别记录设备证据，不能以 Mock/JVM/构建通过替代。
