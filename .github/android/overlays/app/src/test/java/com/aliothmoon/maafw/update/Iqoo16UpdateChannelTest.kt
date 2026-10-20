package com.aliothmoon.maafw.update

import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import kotlin.coroutines.cancellation.CancellationException

class Iqoo16UpdateChannelTest {
    private val newTag = "mine-v3.12.6-iqoo.2190"
    private val apk = "https://github.com/nivranic/MAA_Punish/releases/download/$newTag/MPA-android-v3.12.6-iqoo.2190.apk"

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
    fun `fork routing discards upstream rid and cdk without mutating persisted source`() {
        val check = checkRequest()
        val effective = check.forSelectedChannel()
        assertEquals(UpdateSource.GITHUB, effective.source)
        assertEquals(IQOO16_GITHUB_REPOSITORY, effective.githubRepository)
        assertNull(effective.mirrorchyanRid)
        assertEquals(UpdateSource.MIRRORCHYAN, check.source)
        val resolved = resolveRequest().forSelectedChannel()
        assertEquals(UpdateSource.GITHUB, resolved.source)
        assertNull(resolved.mirrorchyanRid)
        assertNull(resolved.mirrorchyanCdk)
    }

    @Test
    fun `strict mine tags use real SemVer numeric ordering`() {
        val next = iqoo16ReleaseVersion(newTag)!!
        assertTrue(next > iqoo16ReleaseVersion("mine-v3.12.6-iqoo.2189")!!)
        assertTrue(iqoo16ReleaseVersion("mine-v3.12.7-iqoo.2191")!! > next)
        for (tag in listOf("mine-v0.0.0-ci.261020-cb3acf9c", "v3.12.6-iqoo.2190", "mine-v3.12.6-iqoo.0", "mine-v3.12.6-iqoo.02190", "mine-v03.12.6-iqoo.2190")) {
            assertNull(tag, iqoo16ReleaseVersion(tag))
        }
    }

    @Test
    fun `check and resolve use only Pages with mandatory digest and exact release apk`() = runBlocking {
        val gateway = RecordingHttpClientHelper(FakeHttpResponse(200, manifest()), FakeHttpResponse(200, manifest()))
        val service = UpdateService(emptyList(), Iqoo16StaticUpdateClient(gateway.mock, 2189))
        val result = service.check(checkRequest()) as UpdateCheckResult.UpdateAvailable
        assertEquals(UpdateSource.GITHUB, result.source)
        assertEquals(newTag, result.info.version)
        assertEquals("https://github.com/nivranic/MAA_Punish/releases/tag/$newTag", result.info.releaseNotesUrl)
        val resolved = service.resolve(resolveRequest()) as UpdateResolveResult.Resolved
        assertEquals(newTag, resolved.update.version)
        assertEquals(apk, resolved.update.downloadUrl)
        assertEquals("sha256:" + "a".repeat(64), resolved.update.sha256)
        assertEquals(2, gateway.requests.size)
        assertTrue(gateway.requests.all { it.first.startsWith("$IQOO16_MANIFEST_URL?t=") })
        assertTrue(gateway.requests.all { it.second["Cache-Control"] == "no-cache" && "Authorization" !in it.second })
    }

    @Test
    fun `missing static client and failed Pages never fall back to github or mirror`() = runBlocking {
        val unused = object : UpdateSourceClient {
            override val source = UpdateSource.GITHUB
            override suspend fun check(request: UpdateCheckRequest): UpdateCheckResult = error("No REST fallback")
            override suspend fun resolve(request: UpdateResolveRequest): UpdateResolveResult = error("No REST fallback")
        }
        val unconfigured = UpdateService(listOf(unused))
        assertEquals(UpdateCheckFailure.MISSING_CONFIGURATION, (unconfigured.check(checkRequest()) as UpdateCheckResult.SourceFailed).reason)
        assertEquals(UpdateCheckFailure.MISSING_CONFIGURATION, (unconfigured.resolve(resolveRequest()) as UpdateResolveResult.Failed).reason)
        val gateway = RecordingHttpClientHelper(FakeHttpResponse(503, "unavailable"), FakeHttpResponse(404, "missing"))
        val service = UpdateService(listOf(unused), Iqoo16StaticUpdateClient(gateway.mock, 2189))
        assertEquals(UpdateCheckFailure.HTTP, (service.check(checkRequest()) as UpdateCheckResult.SourceFailed).reason)
        assertEquals(UpdateCheckFailure.HTTP, (service.resolve(resolveRequest()) as UpdateResolveResult.Failed).reason)
        assertEquals(2, gateway.requests.size)
    }

    @Test
    fun `same newer or incompatible Android version code cannot download a downgrade`() = runBlocking {
        for ((current, code) in listOf("3.12.6-iqoo.2190" to 2190, "3.12.6-iqoo.2191" to 2191, "3.12.7-iqoo.2192" to 2192, "3.12.5-iqoo.2189" to 2190)) {
            val gateway = RecordingHttpClientHelper(FakeHttpResponse(200, manifest()), FakeHttpResponse(200, manifest()))
            val client = Iqoo16StaticUpdateClient(gateway.mock, code)
            assertTrue(current, client.check(checkRequest().copy(currentVersion = current)) is UpdateCheckResult.UpToDate)
            assertTrue(current, client.resolve(resolveRequest().copy(currentVersion = current)) is UpdateResolveResult.Failed)
        }
    }

    @Test
    fun `hash current version and accidental legacy channel fail before network`() = runBlocking {
        val gateway = RecordingHttpClientHelper()
        val client = Iqoo16StaticUpdateClient(gateway.mock, 2189)
        assertEquals(UpdateCheckFailure.VERSION_INVALID, (client.check(checkRequest().copy(currentVersion = "cb3acf9c")) as UpdateCheckResult.SourceFailed).reason)
        assertEquals(UpdateCheckFailure.VERSION_INVALID, (client.resolve(resolveRequest().copy(currentVersion = "cb3acf9c")) as UpdateResolveResult.Failed).reason)
        assertEquals(UpdateCheckFailure.MISSING_CONFIGURATION, (client.check(checkRequest().copy(channel = UpdateChannel.STABLE)) as UpdateCheckResult.SourceFailed).reason)
        assertTrue(gateway.requests.isEmpty())
    }

    @Test
    fun `malformed schema package versions urls and digest fail as invalid response`() = runBlocking {
        val good = manifest()
        val invalid = listOf(
            "null", "[]", "{", "<html>404</html>",
            good.replace("\"schema_version\":1", "\"schema_version\":2"),
            good.replace("\"schema_version\":1", "\"schema_version\":\"1\""),
            good.replace("\"channel\":\"IQOO16\"", "\"channel\":\"BETA\""),
            good.replace(IQOO16_APPLICATION_ID, "other.package"),
            good.replace("\"version_code\":2190", "\"version_code\":2189"),
            good.replace("\"version_code\":2190", "\"version_code\":\"2190\""),
            good.replace("\"version_name\":\"3.12.6-iqoo.2190\"", "\"version_name\":\"3.12.7-iqoo.2190\""),
            good.replace(apk, apk.replace("https:", "http:")),
            good.replace(apk, "$apk?token=secret"),
            good.replace(apk, apk.replace("nivranic", "someone")),
            good.replace(apk, apk.replace(".apk", "-debug.apk")),
            good.replace("releases/tag/", "releases/other/"),
            good.replace("a".repeat(64), ""),
            good.replace("a".repeat(64), "z".repeat(64)),
            good.replace("a".repeat(64), "sha256:" + "a".repeat(64)),
        )
        for (body in invalid) {
            val gateway = RecordingHttpClientHelper(FakeHttpResponse(200, body), FakeHttpResponse(200, body))
            val client = Iqoo16StaticUpdateClient(gateway.mock, 2189)
            assertEquals(body, UpdateCheckFailure.INVALID_RESPONSE, (client.check(checkRequest()) as UpdateCheckResult.SourceFailed).reason)
            assertEquals(body, UpdateCheckFailure.INVALID_RESPONSE, (client.resolve(resolveRequest()) as UpdateResolveResult.Failed).reason)
        }
    }

    @Test
    fun `network errors are structured but coroutine cancellation propagates`() = runBlocking {
        val gateway = mockk<com.aliothmoon.maafw.util.HttpClientHelper>()
        val client = Iqoo16StaticUpdateClient(gateway, 2189)
        coEvery { gateway.get(any(), any(), any()) } throws IOException("offline")
        assertEquals(UpdateCheckFailure.NETWORK, (client.check(checkRequest()) as UpdateCheckResult.SourceFailed).reason)
        assertEquals(UpdateCheckFailure.NETWORK, (client.resolve(resolveRequest()) as UpdateResolveResult.Failed).reason)
        coEvery { gateway.get(any(), any(), any()) } throws CancellationException("cancel")
        for (resolve in listOf(false, true)) {
            try {
                if (resolve) client.resolve(resolveRequest()) else client.check(checkRequest())
                fail("Cancellation must propagate")
            } catch (_: CancellationException) {
                // Expected, not converted to a network error.
            }
        }
    }

    private fun checkRequest() = UpdateCheckRequest(UpdateSource.MIRRORCHYAN, "3.12.6-iqoo.2189", AndroidAbi.UNIVERSAL, UpdateChannel.IQOO16, "upstream-rid", "overflow65537/MAA_Punish")
    private fun resolveRequest() = UpdateResolveRequest(UpdateSource.MIRRORCHYAN, AndroidAbi.UNIVERSAL, "3.12.6-iqoo.2189", UpdateChannel.IQOO16, "upstream-rid", "must-not-leak", "overflow65537/MAA_Punish")
    private fun manifest() = """{
      "schema_version":1,"channel":"IQOO16","application_id":"$IQOO16_APPLICATION_ID",
      "version_name":"3.12.6-iqoo.2190","version_code":2190,"release_tag":"$newTag",
      "apk_url":"$apk","sha256":"${"a".repeat(64)}",
      "release_url":"https://github.com/nivranic/MAA_Punish/releases/tag/$newTag"
    }"""
}
