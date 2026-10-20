package com.aliothmoon.maafw.update

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class Iqoo16UpdateChannelTest {
    private val oldTag = "mine-v3.12.6-iqoo.2185"
    private val newTag = "mine-v3.12.6-iqoo.2186"

    @Test
    fun `stored channel names and legacy requests retain their meaning`() {
        assertEquals(UpdateChannel.STABLE, UpdateChannel.valueOf("STABLE"))
        assertEquals(UpdateChannel.BETA, UpdateChannel.valueOf("BETA"))
        assertEquals(UpdateChannel.IQOO16, UpdateChannel.valueOf("IQOO16"))
        listOf(UpdateChannel.STABLE, UpdateChannel.BETA).forEach { channel ->
            val check = checkRequest().copy(channel = channel)
            val resolve = resolveRequest().copy(channel = channel)
            assertEquals(check, check.forSelectedChannel())
            assertEquals(resolve, resolve.forSelectedChannel())
            assertEquals(UpdateSource.MIRRORCHYAN, channel.effectiveSource(UpdateSource.MIRRORCHYAN))
        }
    }

    @Test
    fun `fork routing discards upstream rid and cdk without mutating original request`() {
        val check = checkRequest()
        val effective = check.forSelectedChannel()
        assertEquals(UpdateSource.GITHUB, effective.source)
        assertEquals(IQOO16_GITHUB_REPOSITORY, effective.githubRepository)
        assertNull(effective.mirrorchyanRid)
        assertEquals(UpdateSource.MIRRORCHYAN, check.source)
        val resolved = resolveRequest().forSelectedChannel()
        assertEquals(UpdateSource.GITHUB, resolved.source)
        assertEquals(IQOO16_GITHUB_REPOSITORY, resolved.githubRepository)
        assertNull(resolved.mirrorchyanRid)
        assertNull(resolved.mirrorchyanCdk)
    }

    @Test
    fun `only the comparable mine tag format is accepted and commit identifiers order numerically`() {
        val next = iqoo16ReleaseVersion("mine-v3.12.6-iqoo.2190")!!
        assertTrue(next > iqoo16ReleaseVersion("mine-v3.12.6-iqoo.2189")!!)
        assertTrue(iqoo16ReleaseVersion("mine-v3.12.7-iqoo.2191")!! > next)
        assertTrue(iqoo16ReleaseVersion(newTag)!! > UpdateVersion.parse("3.12.6-alpha.7")!!)
        listOf("mine-v0.0.0-ci.261020-cb3acf9c", "v3.12.6-iqoo.2186", "mine-v3.12.6-iqoo.0", "mine-v3.12.6-iqoo.02186").forEach {
            assertNull(it, iqoo16ReleaseVersion(it))
        }
        assertNull(UpdateVersion.parse(newTag))
        assertNull(UpdateVersion.parse("cb3acf9c"))
    }

    @Test
    fun `check and resolve use fork releases without consulting mirror and select the exact apk`() = runBlocking {
        val body = """[
            ${release("v999.0.0", "unrelated.apk")},
            ${release("mine-v0.0.0-ci.261020-cb3acf9c", "legacy.apk")},
            ${release(newTag, "MPA-android-${newTag.removePrefix("mine-")}.apk")},
            ${release(oldTag, "MPA-android-${oldTag.removePrefix("mine-")}-debug.apk")}
        ]"""
        val gateway = RecordingHttpClientHelper(FakeHttpResponse(200, body), FakeHttpResponse(200, body))
        val github = GitHubUpdateClient(GitHubReleasesApi(gateway.mock)) { _, _, _, _ ->
            error("iQOO 16 must not consult MirrorChyan")
        }
        val service = UpdateService(listOf(github))
        val result = service.check(checkRequest()) as UpdateCheckResult.UpdateAvailable
        assertEquals(UpdateSource.GITHUB, result.source)
        assertEquals(newTag, result.info.version)
        val resolved = service.resolve(resolveRequest()) as UpdateResolveResult.Resolved
        assertEquals(newTag, resolved.update.version)
        assertEquals("https://example.com/MPA-android-v3.12.6-iqoo.2186.apk", resolved.update.downloadUrl)
        assertEquals("sha256:" + "a".repeat(64), resolved.update.sha256)
        assertTrue(gateway.requests.all { it.first.startsWith("https://api.github.com/repos/nivranic/MAA_Punish/releases?") })
        assertTrue(gateway.requests.all { "Authorization" !in it.second })
    }

    @Test
    fun `current version and newer installed builds are not offered an older release`() = runBlocking {
        val body = "[${release(newTag, "MPA-android-v3.12.6-iqoo.2186.apk")}]"
        for (current in listOf("3.12.6-iqoo.2186", "3.12.6-iqoo.2187", "3.12.7-iqoo.2190")) {
            val gateway = RecordingHttpClientHelper(FakeHttpResponse(200, body))
            val client = GitHubUpdateClient(GitHubReleasesApi(gateway.mock))
            assertTrue(client.check(checkRequest().forSelectedChannel().copy(currentVersion = current)) is UpdateCheckResult.UpToDate)
        }
    }

    @Test
    fun `legacy hash version is rejected rather than treated as zero`() = runBlocking {
        val gateway = RecordingHttpClientHelper()
        val result = GitHubUpdateClient(GitHubReleasesApi(gateway.mock)).check(
            checkRequest().forSelectedChannel().copy(currentVersion = "cb3acf9c"),
        ) as UpdateCheckResult.SourceFailed
        assertEquals(UpdateCheckFailure.VERSION_INVALID, result.reason)
        assertTrue(gateway.requests.isEmpty())
    }

    @Test
    fun `unrelated and ambiguous apks are rejected`() {
        val api = GitHubReleasesApi(RecordingHttpClientHelper().mock)
        fun asset(name: String) = GitHubReleasesApi.Asset(name, "https://example.com/$name", null)
        val wrong = GitHubReleasesApi.Release(newTag, null, null, listOf(asset("other.apk")), true)
        assertNull(api.latestEligible(listOf(wrong), UpdateChannel.IQOO16, AndroidAbi.UNIVERSAL))
        val ambiguous = wrong.copy(assets = listOf(asset("MPA-android-v3.12.6-iqoo.2186.apk"), asset("MPA-android-v3.12.6-iqoo.2186-debug.apk")))
        assertNull(api.latestEligible(listOf(ambiguous), UpdateChannel.IQOO16, AndroidAbi.UNIVERSAL))
        assertFalse("MPA-android-v3.12.6-iqoo.2185.apk".isIqoo16ApkFor(newTag))
    }

    private fun checkRequest() = UpdateCheckRequest(
        source = UpdateSource.MIRRORCHYAN,
        currentVersion = "3.12.6-iqoo.2185",
        abi = AndroidAbi.UNIVERSAL,
        channel = UpdateChannel.IQOO16,
        mirrorchyanRid = "upstream-rid",
        githubRepository = "overflow65537/MAA_Punish",
    )

    private fun resolveRequest() = UpdateResolveRequest(
        source = UpdateSource.MIRRORCHYAN,
        currentVersion = "3.12.6-iqoo.2185",
        abi = AndroidAbi.UNIVERSAL,
        channel = UpdateChannel.IQOO16,
        mirrorchyanRid = "upstream-rid",
        mirrorchyanCdk = "must-not-leak",
        githubRepository = "overflow65537/MAA_Punish",
    )

    private fun release(tag: String, apk: String): String = """{
      "tag_name":"$tag", "prerelease":true,
      "assets":[{"name":"$apk","state":"uploaded","browser_download_url":"https://example.com/$apk","digest":"sha256:${"a".repeat(64)}"}]
    }"""
}
