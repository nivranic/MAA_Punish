package com.aliothmoon.maafw.update

/** The standalone package cannot activate an official channel, even through an imported backup. */
class UpdateChannelPolicy(val iqoo16Only: Boolean = false) {
    fun channel(selected: UpdateChannel): UpdateChannel =
        if (iqoo16Only) UpdateChannel.IQOO16 else selected

    fun source(selected: UpdateSource): UpdateSource =
        if (iqoo16Only) UpdateSource.GITHUB else selected

    fun cdk(selected: String): String = if (iqoo16Only) "" else selected

    fun effectiveSource(selectedChannel: UpdateChannel, selectedSource: UpdateSource): UpdateSource =
        channel(selectedChannel).effectiveSource(source(selectedSource))

    fun check(request: UpdateCheckRequest): UpdateCheckRequest =
        request.copy(channel = channel(request.channel), source = source(request.source)).forSelectedChannel()

    fun resolve(request: UpdateResolveRequest): UpdateResolveRequest =
        request.copy(channel = channel(request.channel), source = source(request.source)).forSelectedChannel()
}
