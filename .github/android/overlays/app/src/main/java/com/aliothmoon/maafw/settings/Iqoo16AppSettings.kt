package com.aliothmoon.maafw.settings

import com.aliothmoon.maafw.update.UpdateChannel
import com.aliothmoon.maafw.update.UpdateChannelPolicy
import com.aliothmoon.maafw.update.UpdateSource

/** Apply to defaults and every DataStore emission, including imported or restored settings. */
internal fun AppSettings.forUpdatePolicy(policy: UpdateChannelPolicy): AppSettings =
    if (policy.iqoo16Only) copy(
        updateChannel = UpdateChannel.IQOO16.name,
        updateSource = UpdateSource.GITHUB.name,
        mirrorchyanCdk = "",
    ) else this
