"""固定 bc55dac MaaFwApp 的精确文本锚点；升级上游时需重新审查与运行真实 Kotlin 测试。"""

KOTLIN = "app/src/main/java/com/aliothmoon/maafw/"

# (相对路径, 原文, 替换文)。通用构建保持原语义，独立包由 DI 的 policy 限定渠道。
PATCHES = (
    (
        "build-logic/convention/src/main/kotlin/com/aliothmoon/maafw/gradle/AndroidApplicationConventionPlugin.kt",
        'private const val BASE_APPLICATION_ID = "com.aliothmoon.maafw"',
        'private const val BASE_APPLICATION_ID = "com.overflow65537.maafw"',
    ),
    (
        KOTLIN + "update/UpdateContracts.kt",
        "enum class UpdateChannel {\n    STABLE,\n    BETA,\n}",
        "enum class UpdateChannel {\n    STABLE,\n    BETA,\n    IQOO16,\n}",
    ),
    (
        KOTLIN + "update/UpdateVersion.kt",
        "    UpdateChannel.BETA -> true\n}",
        "    UpdateChannel.BETA -> true\n    UpdateChannel.IQOO16 -> true\n}",
    ),
    (
        KOTLIN + "update/UpdateService.kt",
        "    clients: Collection<UpdateSourceClient>,\n",
        "    clients: Collection<UpdateSourceClient>,\n    private val iqoo16Client: UpdateSourceClient? = null,\n    private val updatePolicy: UpdateChannelPolicy = UpdateChannelPolicy(),\n",
    ),
    (
        KOTLIN + "update/UpdateService.kt",
        '        val source = request.source\n        Timber.tag("UpdateCheck").i(',
        '        val selected = updatePolicy.check(request)\n        val source = selected.source\n        Timber.tag("UpdateCheck").i(',
    ),
    (
        KOTLIN + "update/UpdateService.kt",
        "return clientsBySource.getValue(source).check(request)",
        """return if (selected.channel == UpdateChannel.IQOO16) {
            iqoo16Client?.check(selected)
                ?: UpdateCheckResult.SourceFailed(source, UpdateCheckFailure.MISSING_CONFIGURATION)
        } else {
            clientsBySource.getValue(source).check(selected)
        }""",
    ),
    (
        KOTLIN + "update/UpdateService.kt",
        '''        Timber.tag("UpdateResolve").i("source=%s channel=%s", request.source, request.channel)
        return clientsBySource.getValue(request.source).resolve(request)''',
        '''        val selected = updatePolicy.resolve(request)
        Timber.tag("UpdateResolve").i("source=%s channel=%s", selected.source, selected.channel)
        return if (selected.channel == UpdateChannel.IQOO16) {
            iqoo16Client?.resolve(selected)
                ?: UpdateResolveResult.Failed(selected.source, UpdateCheckFailure.MISSING_CONFIGURATION)
        } else {
            clientsBySource.getValue(selected.source).resolve(selected)
        }''',
    ),
    (
        "app/src/main/java/com/aliothmoon/maafw/di/UpdateModule.kt",
        "import com.aliothmoon.maafw.update.GitHubUpdateClient\n",
        "import com.aliothmoon.maafw.update.GitHubUpdateClient\nimport com.aliothmoon.maafw.update.Iqoo16StaticUpdateClient\n",
    ),
    (
        "app/src/main/java/com/aliothmoon/maafw/di/UpdateModule.kt",
        "    single { GitHubReleasesApi(get()) }\n",
        "    single { GitHubReleasesApi(get()) }\n    single { Iqoo16StaticUpdateClient(get()) }\n",
    ),
    (
        "app/src/main/java/com/aliothmoon/maafw/di/UpdateModule.kt",
        "                get<GitHubUpdateClient>(),\n            ),\n",
        "                get<GitHubUpdateClient>(),\n            ),\n            iqoo16Client = get<Iqoo16StaticUpdateClient>(),\n            updatePolicy = get(),\n",
    ),
    (
        KOTLIN + "settings/SettingsViewModel.kt",
        "import com.aliothmoon.maafw.update.UpdateSource\n",
        "import com.aliothmoon.maafw.update.UpdateSource\nimport com.aliothmoon.maafw.update.UpdateChannelPolicy\n",
    ),
    (
        KOTLIN + "settings/SettingsViewModel.kt",
        "            updateSource = source,\n            mirrorchyanCdk = cdk,",
        "            updateSource = updatePolicy.effectiveSource(channel, source),\n            mirrorchyanCdk = updatePolicy.cdk(cdk),",
    ),
    (
        KOTLIN + "settings/SettingsViewModel.kt",
        "                appSettings.setUpdateChannel(intent.channel)\n            }",
        '''                beginUpdateSelectionChange()
                try {
                    val channel = updatePolicy.channel(intent.channel)
                    appSettings.setUpdateChannel(channel)
                    appSettings.updateChannel.first { it == channel }
                } finally {
                    updateOperation.update { it.copy(checking = false) }
                }
            }''',
    ),
    (
        KOTLIN + "settings/SettingsViewModel.kt",
        "                appSettings.setUpdateSource(intent.source)\n            }",
        '''                beginUpdateSelectionChange()
                try {
                    val source = updatePolicy.source(intent.source)
                    appSettings.setUpdateSource(source)
                    appSettings.updateSource.first { it == source }
                } finally {
                    updateOperation.update { it.copy(checking = false) }
                }
            }''',
    ),
    (
        KOTLIN + "settings/SettingsViewModel.kt",
        "    private fun updateSettingsLocked(): Boolean = updateOperation.value.downloading",
        '''    private fun updateSettingsLocked(): Boolean =
        updateOperation.value.downloading || updateOperation.value.checking

    private fun beginUpdateSelectionChange() {
        updateOperation.update {
            it.copy(checking = true, checkResult = null, updatePrompt = null, errorMessage = null, errorPrompt = null)
        }
    }''',
    ),
    (
        KOTLIN + "settings/SettingsViewModel.kt",
        "        val source = appSettings.updateSource.value\n        val cdk = appSettings.mirrorchyanCdk.value",
        "        val source = updatePolicy.effectiveSource(appSettings.updateChannel.value, appSettings.updateSource.value)\n        val cdk = updatePolicy.cdk(appSettings.mirrorchyanCdk.value)",
    ),
    (
        KOTLIN + "ui/home/UpdateSection.kt",
        "import androidx.compose.foundation.layout.Arrangement\n",
        "import androidx.compose.foundation.layout.Arrangement\nimport androidx.compose.foundation.layout.FlowRow\n",
    ),
    (
        KOTLIN + "ui/home/UpdateSection.kt",
        "    val settingsEnabled = !update.downloading\n",
        "    val settingsEnabled = !update.downloading && !update.checking\n",
    ),
    (
        KOTLIN + "ui/home/UpdateSection.kt",
        '''                label = source.updateSourceLabel(),
                selected = update.updateSource == source,
                enabled = settingsEnabled,''',
        '''                label = source.updateSourceLabel(),
                selected = update.updateSource == source,
                enabled = settingsEnabled && update.channel != UpdateChannel.IQOO16,''',
    ),
    (
        KOTLIN + "ui/home/UpdateSection.kt",
        '''        UpdateRowLabel(stringResource(R.string.settings_update_channel))
        Spacer(Modifier.weight(1f))
        listOf(
            UpdateChannel.STABLE to stringResource(R.string.settings_update_channel_stable),
            UpdateChannel.BETA to stringResource(R.string.settings_update_channel_beta),
        ).forEach { (channel, label) ->
            MaaChoiceChip(
                label = label,
                selected = update.channel == channel,
                enabled = settingsEnabled,
                role = Role.RadioButton,
                onClick = { onSettingsIntent(SettingsIntent.SetUpdateChannel(channel)) },
            )
        }''',
        '''        UpdateRowLabel(stringResource(R.string.settings_update_channel))
        FlowRow(
            modifier = Modifier.weight(1f),
            horizontalArrangement = Arrangement.spacedBy(MaaDesignTokens.Spacing.xs, Alignment.End),
            verticalArrangement = Arrangement.spacedBy(MaaDesignTokens.Spacing.xs),
        ) {
            (if (update.iqoo16Only) listOf(
                UpdateChannel.IQOO16 to stringResource(R.string.settings_update_channel_iqoo16),
            ) else listOf(
                UpdateChannel.STABLE to stringResource(R.string.settings_update_channel_stable),
                UpdateChannel.BETA to stringResource(R.string.settings_update_channel_beta),
                UpdateChannel.IQOO16 to stringResource(R.string.settings_update_channel_iqoo16),
            )).forEach { (channel, label) ->
                MaaChoiceChip(
                    label = label,
                    selected = update.channel == channel,
                    enabled = settingsEnabled,
                    role = Role.RadioButton,
                    onClick = { onSettingsIntent(SettingsIntent.SetUpdateChannel(channel)) },
                )
            }
        }''',
    ),
    (
        "app/src/main/res/values/strings.xml",
        '    <string name="settings_update_channel_beta">公测版</string>\n',
        '    <string name="settings_update_channel_beta">公测版</string>\n    <string name="settings_update_channel_iqoo16" translatable="false">iQOO 16 适配版</string>\n',
    ),
)

# USB development sessions are opt-in through a foreground App + authorized
# USB CLI. Keep the protocol separate from the privileged service heartbeat.
PATCHES += (
    (
        KOTLIN + "MaaFwApp.kt",
        "import com.aliothmoon.maafw.constant.AppPaths\n",
        "import com.aliothmoon.maafw.constant.AppPaths\nimport com.aliothmoon.maafw.devsession.DevSessionCoordinator\nimport com.aliothmoon.maafw.devsession.DevReloadCoordinator\nimport com.aliothmoon.maafw.devsession.DevReloadMailboxBridge\n",
    ),
    (
        KOTLIN + "MaaFwApp.kt",
        "        koin.get<ScreenSaverOverlayManager>().setup()\n",
        "        koin.get<ScreenSaverOverlayManager>().setup()\n        koin.get<DevSessionCoordinator>().setup()\n",
    ),
    (
        KOTLIN + "di/CoreModule.kt",
        "import com.aliothmoon.maafw.settings.search.SettingSearchNavigator\n",
        "import com.aliothmoon.maafw.settings.search.SettingSearchNavigator\nimport com.aliothmoon.maafw.devsession.DevSessionCoordinator\nimport com.aliothmoon.maafw.devsession.DevReloadCoordinator\nimport com.aliothmoon.maafw.devsession.DevReloadMailboxBridge\n",
    ),
    (
        KOTLIN + "di/CoreModule.kt",
        "    single { SettingSearchNavigator() }\n",
        """    single { SettingSearchNavigator() }
    single { DevSessionCoordinator(get(named<AppCoroutineScope>())) }
    single { DevReloadCoordinator(androidContext(), get(named<AppCoroutineScope>()), get(), get(), get(), get(), get(), get(), get()) }
    single { DevReloadMailboxBridge(get(), get(), get(named<AppCoroutineScope>())) }
""",
    ),
)

# Standalone policy is injected only for the distinct iQOO package.
PATCHES += (
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsViewModel.kt',
        '''    private val clockMillis: () -> Long = { System.nanoTime() / 1_000_000 },
''',
        '''    private val clockMillis: () -> Long = { System.nanoTime() / 1_000_000 },
    private val updatePolicy: UpdateChannelPolicy = UpdateChannelPolicy(),
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsViewModel.kt',
        '    private val updateOperation = MutableStateFlow(UpdatePanelState())',
        '''    private val initialUpdatePanel = UpdatePanelState(
        iqoo16Only = updatePolicy.iqoo16Only,
        channel = updatePolicy.channel(com.aliothmoon.maafw.update.UpdateChannel.STABLE),
        updateSource = updatePolicy.source(UpdateSource.MIRRORCHYAN),
    )
    private val updateOperation = MutableStateFlow(initialUpdatePanel)''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsViewModel.kt',
        '''            channel = channel,
''',
        '''            channel = updatePolicy.channel(channel),
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsViewModel.kt',
        '        initialValue = SettingsUiState(),',
        '        initialValue = SettingsUiState(update = initialUpdatePanel),',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsViewModel.kt',
        '                appSettings.setMirrorchyanCdk(intent.cdk)',
        '                appSettings.setMirrorchyanCdk(updatePolicy.cdk(intent.cdk))',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsViewModel.kt',
        '''UpdateCheckRequest(
                            source = appSettings.updateSource.value,
                            currentVersion = currentVersion,
                            channel = appSettings.updateChannel.value,
                            abi = abi,
                            mirrorchyanRid = mirrorchyanRid(metadata),
                            githubRepository = metadata?.githubRepository,
                        )''',
        '''updatePolicy.check(UpdateCheckRequest(
                            source = appSettings.updateSource.value,
                            currentVersion = currentVersion,
                            channel = appSettings.updateChannel.value,
                            abi = abi,
                            mirrorchyanRid = mirrorchyanRid(metadata),
                            githubRepository = metadata?.githubRepository,
                        ))''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsViewModel.kt',
        '''UpdateCheckRequest(
                source = appSettings.updateSource.value,
                currentVersion = currentVersion,
                channel = appSettings.updateChannel.value,
                abi = abi,
                mirrorchyanRid = mirrorchyanRid(metadata),
                githubRepository = metadata.githubRepository,
            )''',
        '''updatePolicy.check(UpdateCheckRequest(
                source = appSettings.updateSource.value,
                currentVersion = currentVersion,
                channel = appSettings.updateChannel.value,
                abi = abi,
                mirrorchyanRid = mirrorchyanRid(metadata),
                githubRepository = metadata.githubRepository,
            ))''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsViewModel.kt',
        '''UpdateCheckRequest(
                source = appSettings.updateSource.value,
                currentVersion = currentVersion,
                channel = appSettings.updateChannel.value,
                abi = abi,
                mirrorchyanRid = mirrorchyanRid(metadata),
                githubRepository = metadata?.githubRepository,
            )''',
        '''updatePolicy.check(UpdateCheckRequest(
                source = appSettings.updateSource.value,
                currentVersion = currentVersion,
                channel = appSettings.updateChannel.value,
                abi = abi,
                mirrorchyanRid = mirrorchyanRid(metadata),
                githubRepository = metadata?.githubRepository,
            ))''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsViewModel.kt',
        '''UpdateResolveRequest(
                    source = source,
                    channel = appSettings.updateChannel.value,
                    abi = abi,
                    currentVersion = currentVersion,
                    mirrorchyanRid = mirrorchyanRid(metadata),
                    mirrorchyanCdk = cdk.takeIf(String::isNotBlank),
                    githubRepository = metadata?.githubRepository,
                )''',
        '''updatePolicy.resolve(UpdateResolveRequest(
                    source = source,
                    channel = appSettings.updateChannel.value,
                    abi = abi,
                    currentVersion = currentVersion,
                    mirrorchyanRid = mirrorchyanRid(metadata),
                    mirrorchyanCdk = cdk.takeIf(String::isNotBlank),
                    githubRepository = metadata?.githubRepository,
                ))''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/SettingsContracts.kt',
        '''    val errorMessage: UiText? = null,
) {''',
        '''    val errorMessage: UiText? = null,
    val iqoo16Only: Boolean = false,
) {''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/AppSettingsManager.kt',
        '''import com.aliothmoon.maafw.update.UpdateChannel
''',
        '''import com.aliothmoon.maafw.update.UpdateChannel
import com.aliothmoon.maafw.update.UpdateChannelPolicy
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/AppSettingsManager.kt',
        '''import kotlinx.coroutines.flow.first
''',
        '''import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/AppSettingsManager.kt',
        'class AppSettingsManager(private val context: Context) : AppSettingsGateway {',
        '''class AppSettingsManager(
    private val context: Context,
    private val updatePolicy: UpdateChannelPolicy = UpdateChannelPolicy(),
) : AppSettingsGateway {''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/AppSettingsManager.kt',
        '''    val settings: Flow<AppSettings> = with(AppSettingsSchema) { context.dataStore.flow }

    private val defaults = AppSettings()''',
        '''    val settings: Flow<AppSettings> = with(AppSettingsSchema) { context.dataStore.flow }
        .map { it.forUpdatePolicy(updatePolicy) }

    private val defaults = AppSettings().forUpdatePolicy(updatePolicy)''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/AppSettingsManager.kt',
        'it[updateChannel] = channel.name',
        'it[updateChannel] = updatePolicy.channel(channel).name',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/AppSettingsManager.kt',
        'it[updateSource] = source.name',
        'it[updateSource] = updatePolicy.source(source).name',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/AppSettingsManager.kt',
        'it[mirrorchyanCdk] = cdk.trim()',
        'it[mirrorchyanCdk] = updatePolicy.cdk(cdk.trim())',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/settings/AppSettingsManager.kt',
        '            PORTABLE_SETTINGS.forEach { (key, _) -> values[key.name]?.let { prefs[key] = it } }',
        '''            PORTABLE_SETTINGS.forEach { (key, _) -> values[key.name]?.let { prefs[key] = it } }
            if (updatePolicy.iqoo16Only) {
                prefs[AppSettingsSchema.updateChannel] = UpdateChannel.IQOO16.name
                prefs[AppSettingsSchema.updateSource] = UpdateSource.GITHUB.name
                prefs[AppSettingsSchema.mirrorchyanCdk] = ""
            }''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/di/CoreModule.kt',
        '''import com.aliothmoon.maafw.settings.AppSettingsManager
''',
        '''import com.aliothmoon.maafw.settings.AppSettingsManager
import com.aliothmoon.maafw.update.IQOO16_APPLICATION_ID
import com.aliothmoon.maafw.update.UpdateChannelPolicy
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/di/CoreModule.kt',
        '    single { AppSettingsManager(androidContext()) }',
        '''    single { UpdateChannelPolicy(BuildConfig.APPLICATION_ID == IQOO16_APPLICATION_ID) }
    single { AppSettingsManager(androidContext(), get()) }''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/di/ViewModelModule.kt',
        '''            apkInstaller = get(),
''',
        '''            apkInstaller = get(),
            updatePolicy = get(),
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/ui/home/UpdateSection.kt',
        '        UpdateSource.entries.forEach { source ->',
        '        (if (update.iqoo16Only) listOf(UpdateSource.GITHUB) else UpdateSource.entries).forEach { source ->',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/ui/home/UpdateSection.kt',
        '''        IconButton(
            onClick = { mirrorInfoVisible = true },
            modifier = Modifier.size(MaaDesignTokens.IconContainer.sm),
        ) {
            Icon(
                imageVector = Icons.Outlined.Info,
                contentDescription = stringResource(R.string.settings_update_mirror_about),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(MaaDesignTokens.IconSize.sm),
            )
        }
''',
        '''        if (!update.iqoo16Only) {
            IconButton(
                onClick = { mirrorInfoVisible = true },
                modifier = Modifier.size(MaaDesignTokens.IconContainer.sm),
            ) {
                Icon(
                    imageVector = Icons.Outlined.Info,
                    contentDescription = stringResource(R.string.settings_update_mirror_about),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(MaaDesignTokens.IconSize.sm),
                )
            }
        }
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/ui/home/UpdateSection.kt',
        '    if (mirrorInfoVisible) {',
        '    if (mirrorInfoVisible && !update.iqoo16Only) {',
    ),
)

from maafwapp_screensaver_patches import PATCHES as SCREENSAVER_PATCHES

PATCHES += SCREENSAVER_PATCHES

from maafwapp_reload_patches import RELOAD_PATCHES

PATCHES += RELOAD_PATCHES

from maafwapp_recovery_patches import RECOVERY_PATCHES

PATCHES += RECOVERY_PATCHES

PATCHES += (
    (
        KOTLIN + "MaaFwApp.kt",
        "    fun postCreate(koin: Koin) {\n",
        "    fun postCreate(koin: Koin) {\n        koin.get<DevReloadCoordinator>().setup()\n        koin.get<DevReloadMailboxBridge>().setup()\n",
    ),
)
