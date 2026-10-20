"""固定 bc55dac MaaFwApp 的精确文本锚点；升级上游时需重新审查与运行真实 Kotlin 测试。"""

KOTLIN = "app/src/main/java/com/aliothmoon/maafw/"

# (相对路径, 原文, 替换文)。原渠道枚举名及设置默认值保持原样。
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
        '        val source = request.source\n        Timber.tag("UpdateCheck").i(',
        '        val selected = request.forSelectedChannel()\n        val source = selected.source\n        Timber.tag("UpdateCheck").i(',
    ),
    (
        KOTLIN + "update/UpdateService.kt",
        "return clientsBySource.getValue(source).check(request)",
        "return clientsBySource.getValue(source).check(selected)",
    ),
    (
        KOTLIN + "update/UpdateService.kt",
        '''        Timber.tag("UpdateResolve").i("source=%s channel=%s", request.source, request.channel)
        return clientsBySource.getValue(request.source).resolve(request)''',
        '''        val selected = request.forSelectedChannel()
        Timber.tag("UpdateResolve").i("source=%s channel=%s", selected.source, selected.channel)
        return clientsBySource.getValue(selected.source).resolve(selected)''',
    ),
    (
        KOTLIN + "update/GitHubUpdate.kt",
        '''                val version = UpdateVersion.parse(candidate.tag) ?: return@mapNotNull null
                if (!version.allowedFor(channel)) return@mapNotNull null
                if (channel == UpdateChannel.STABLE && candidate.prerelease) return@mapNotNull null
                val asset = selectAsset(candidate.assets, abi) ?: return@mapNotNull null''',
        '''                val version = if (channel == UpdateChannel.IQOO16) {
                    iqoo16ReleaseVersion(candidate.tag)
                } else {
                    UpdateVersion.parse(candidate.tag)
                } ?: return@mapNotNull null
                if (!version.allowedFor(channel)) return@mapNotNull null
                if (channel == UpdateChannel.STABLE && candidate.prerelease) return@mapNotNull null
                val assets = if (channel == UpdateChannel.IQOO16) {
                    candidate.assets.filter { it.name.isIqoo16ApkFor(candidate.tag) }
                } else {
                    candidate.assets
                }
                val asset = selectAsset(assets, abi) ?: return@mapNotNull null''',
    ),
    (
        KOTLIN + "update/GitHubUpdate.kt",
        '''    ): Pair<MirrorChyanLatestApi.Latest, Version>? {
        val latest = mirrorRelease(rid, channel, abi, currentVersion) ?: return null''',
        '''    ): Pair<MirrorChyanLatestApi.Latest, Version>? {
        // The fork channel must never pin itself to the upstream MirrorChyan RID.
        if (channel == UpdateChannel.IQOO16) return null
        val latest = mirrorRelease(rid, channel, abi, currentVersion) ?: return null''',
    ),
    (
        KOTLIN + "settings/SettingsViewModel.kt",
        "import com.aliothmoon.maafw.update.UpdateSource\n",
        "import com.aliothmoon.maafw.update.UpdateSource\nimport com.aliothmoon.maafw.update.effectiveSource\n",
    ),
    (
        KOTLIN + "settings/SettingsViewModel.kt",
        "            updateSource = source,\n            mirrorchyanCdk = cdk,",
        "            updateSource = channel.effectiveSource(source),\n            mirrorchyanCdk = cdk,",
    ),
    (
        KOTLIN + "settings/SettingsViewModel.kt",
        "                appSettings.setUpdateChannel(intent.channel)\n            }",
        '''                beginUpdateSelectionChange()
                try {
                    appSettings.setUpdateChannel(intent.channel)
                    appSettings.updateChannel.first { it == intent.channel }
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
                    appSettings.setUpdateSource(intent.source)
                    appSettings.updateSource.first { it == intent.source }
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
        "        val source = appSettings.updateChannel.value.effectiveSource(appSettings.updateSource.value)\n        val cdk = appSettings.mirrorchyanCdk.value",
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
            listOf(
                UpdateChannel.STABLE to stringResource(R.string.settings_update_channel_stable),
                UpdateChannel.BETA to stringResource(R.string.settings_update_channel_beta),
                UpdateChannel.IQOO16 to stringResource(R.string.settings_update_channel_iqoo16),
            ).forEach { (channel, label) ->
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
