package com.aliothmoon.maafw.update

import com.aliothmoon.maafw.BuildConfig
import com.aliothmoon.maafw.R
import com.aliothmoon.maafw.i18n.uiTextOf
import com.aliothmoon.maafw.util.HttpClientHelper
import com.aliothmoon.maafw.util.readBody
import io.github.z4kn4fein.semver.Version
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import timber.log.Timber
import kotlin.coroutines.cancellation.CancellationException

/** The Pages document describes one universal, signed APK; it is never a GitHub API credential. */
internal data class Iqoo16Manifest(
    val version: Version,
    val versionCode: Int,
    val releaseTag: String,
    val apkUrl: String,
    val sha256: String,
    val releaseUrl: String,
) {
    companion object {
        fun parse(body: String): Iqoo16Manifest? = try {
            val raw = Json.parseToJsonElement(body) as? JsonObject
            if (raw == null) null else fromJson(raw)
        } catch (_: IllegalArgumentException) {
            null
        }

        private fun fromJson(raw: JsonObject): Iqoo16Manifest? {
            if (raw.integer("schema_version") != 1 || raw.text("channel") != "IQOO16" ||
                raw.text("application_id") != IQOO16_APPLICATION_ID
            ) return null
            val tag = raw.text("release_tag") ?: return null
            val version = iqoo16ReleaseVersion(tag) ?: return null
            if (raw.text("version_name") != tag.removePrefix("mine-v")) return null
            val code = raw.integer("version_code") ?: return null
            if (code !in 1..2_100_000_000 || tag.substringAfterLast('.') != code.toString()) return null
            val apkUrl = "https://github.com/$IQOO16_GITHUB_REPOSITORY/releases/download/$tag/MPA-android-iqoo16-${tag.removePrefix("mine-")}.apk"
            val releaseUrl = "https://github.com/$IQOO16_GITHUB_REPOSITORY/releases/tag/$tag"
            if (raw.text("apk_url") != apkUrl || raw.text("release_url") != releaseUrl) return null
            val sha256 = raw.text("sha256")?.takeIf { it.matches(Regex("[0-9a-f]{64}")) } ?: return null
            return Iqoo16Manifest(version, code, tag, apkUrl, sha256, releaseUrl)
        }

        private fun JsonObject.text(name: String): String? =
            (get(name) as? JsonPrimitive)?.takeIf { it.isString }?.content

        private fun JsonObject.integer(name: String): Int? =
            (get(name) as? JsonPrimitive)?.takeIf { !it.isString }?.content?.toIntOrNull()
    }
}

/** Only the dedicated channel uses this endpoint. Failure never falls back to REST or MirrorChyan. */
class Iqoo16StaticUpdateClient(
    private val helper: HttpClientHelper,
    private val installedVersionCode: Int = BuildConfig.VERSION_CODE,
) : UpdateSourceClient {
    override val source: UpdateSource = UpdateSource.GITHUB

    override suspend fun check(request: UpdateCheckRequest): UpdateCheckResult {
        if (request.channel != UpdateChannel.IQOO16) {
            return UpdateCheckResult.SourceFailed(source, UpdateCheckFailure.MISSING_CONFIGURATION)
        }
        val current = UpdateVersion.parse(request.currentVersion)
            ?: return UpdateCheckResult.SourceFailed(source, UpdateCheckFailure.VERSION_INVALID)
        return when (val outcome = manifest()) {
            is UpdateSourceOutcome.Failed -> UpdateCheckResult.SourceFailed(source, outcome.reason, outcome.detail)
            is UpdateSourceOutcome.Ok -> {
                val latest = outcome.value
                if (latest.version <= current || latest.versionCode <= installedVersionCode) {
                    UpdateCheckResult.UpToDate(source, latest.releaseTag)
                } else {
                    UpdateCheckResult.UpdateAvailable(source, UpdateInfo(latest.releaseTag, latest.releaseUrl))
                }
            }
        }
    }

    override suspend fun resolve(request: UpdateResolveRequest): UpdateResolveResult {
        if (request.channel != UpdateChannel.IQOO16) {
            return UpdateResolveResult.Failed(source, UpdateCheckFailure.MISSING_CONFIGURATION)
        }
        val current = UpdateVersion.parse(request.currentVersion)
            ?: return UpdateResolveResult.Failed(source, UpdateCheckFailure.VERSION_INVALID)
        return when (val outcome = manifest()) {
            is UpdateSourceOutcome.Failed -> UpdateResolveResult.Failed(source, outcome.reason, outcome.detail)
            is UpdateSourceOutcome.Ok -> {
                val latest = outcome.value
                if (latest.version <= current || latest.versionCode <= installedVersionCode) {
                    UpdateResolveResult.Failed(source, UpdateCheckFailure.NO_MATCHING_ASSET)
                } else {
                    UpdateResolveResult.Resolved(
                        ResolvedUpdate(source, latest.releaseTag, latest.apkUrl, "sha256:${latest.sha256}"),
                    )
                }
            }
        }
    }

    private suspend fun manifest(): UpdateSourceOutcome<Iqoo16Manifest> = try {
        // One request per existing check/resolve. A minute bucket avoids a stale Pages edge cache
        // without changing the application's polling schedule or embedding a token.
        val response = helper.get(
            IQOO16_MANIFEST_URL,
            query = mapOf("t" to (System.currentTimeMillis() / 60_000).toString()),
            headers = mapOf("Accept" to "application/json", "Cache-Control" to "no-cache", "Pragma" to "no-cache"),
        )
        val status = response.code
        val body = response.readBody()
        if (status !in 200..299) {
            UpdateSourceOutcome.Failed(
                if (status == 429) UpdateCheckFailure.RATE_LIMITED else UpdateCheckFailure.HTTP,
                uiTextOf(R.string.update_detail_http_status, status),
            )
        } else {
            Iqoo16Manifest.parse(body)?.let { UpdateSourceOutcome.Ok(it) }
                ?: UpdateSourceOutcome.Failed(UpdateCheckFailure.INVALID_RESPONSE)
        }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        Timber.tag("UpdateCheck").w(e, "iQOO 16 static manifest request failed")
        UpdateSourceOutcome.Failed(UpdateCheckFailure.NETWORK)
    }
}
