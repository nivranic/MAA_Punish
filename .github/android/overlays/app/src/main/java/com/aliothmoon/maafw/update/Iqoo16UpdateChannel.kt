package com.aliothmoon.maafw.update

import io.github.z4kn4fein.semver.Version

internal const val IQOO16_GITHUB_REPOSITORY = "nivranic/MAA_Punish"

/** Keep the stored source for the original channels; the fork uses GitHub exclusively. */
internal fun UpdateChannel.effectiveSource(selected: UpdateSource): UpdateSource =
    if (this == UpdateChannel.IQOO16) UpdateSource.GITHUB else selected

internal fun UpdateCheckRequest.forSelectedChannel(): UpdateCheckRequest =
    if (channel == UpdateChannel.IQOO16) copy(
        source = UpdateSource.GITHUB,
        githubRepository = IQOO16_GITHUB_REPOSITORY,
        mirrorchyanRid = null,
    ) else this

internal fun UpdateResolveRequest.forSelectedChannel(): UpdateResolveRequest =
    if (channel == UpdateChannel.IQOO16) copy(
        source = UpdateSource.GITHUB,
        githubRepository = IQOO16_GITHUB_REPOSITORY,
        mirrorchyanRid = null,
        mirrorchyanCdk = null,
    ) else this

// Historical date/SHA tags did not describe the APK version and cannot be compared safely.
private val IQOO16_TAG = Regex("""^mine-v\d+\.\d+\.\d+-iqoo\.[1-9]\d*$""")

internal fun iqoo16ReleaseVersion(tag: String): Version? =
    tag.takeIf(IQOO16_TAG::matches)?.removePrefix("mine-")?.let(UpdateVersion::parse)

/** Match the selected release, never another APK or desktop asset in the same release. */
internal fun String.isIqoo16ApkFor(tag: String): Boolean {
    val base = "MPA-android-${tag.removePrefix("mine-")}"
    return this == "$base.apk" || this == "$base-debug.apk"
}
