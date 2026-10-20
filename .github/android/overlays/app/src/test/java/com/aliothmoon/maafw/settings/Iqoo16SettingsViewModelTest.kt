package com.aliothmoon.maafw.settings

import com.aliothmoon.maafw.SystemApkInstaller
import com.aliothmoon.maafw.domain.ControllerDefinition
import com.aliothmoon.maafw.domain.ProjectDefinition
import com.aliothmoon.maafw.domain.ProjectMetadata
import com.aliothmoon.maafw.privileged.FakePermissionGateway
import com.aliothmoon.maafw.project.FakeProjectRepository
import com.aliothmoon.maafw.project.ProjectState
import com.aliothmoon.maafw.update.*
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class Iqoo16SettingsViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    @Test
    fun `fork channel downloads without cdk and switching back restores stored source`() = runTest {
        val settings = FakeAppSettingsGateway()
        val resolves = mutableListOf<UpdateResolveRequest>()
        val service = mockk<UpdateService> {
            coEvery { check(any()) } returns UpdateCheckResult.UpdateAvailable(UpdateSource.GITHUB, UpdateInfo("v3.12.6-iqoo.2186"))
            coEvery { resolve(any()) } coAnswers {
                resolves += firstArg<UpdateResolveRequest>()
                UpdateResolveResult.Failed(UpdateSource.GITHUB, UpdateCheckFailure.NETWORK)
            }
        }
        val vm = viewModel(settings, service)
        vm.onIntent(SettingsIntent.SetUpdateChannel(UpdateChannel.IQOO16))
        vm.onIntent(SettingsIntent.CheckUpdate)
        vm.onIntent(SettingsIntent.DownloadUpdate)
        advanceUntilIdle()
        assertEquals(UpdateSource.GITHUB, resolves.single().source)
        assertNull(resolves.single().mirrorchyanCdk)
        assertEquals(UpdateSource.MIRRORCHYAN, settings.updateSource.value)
        assertEquals(UpdateSource.GITHUB, vm.uiState.first().update.updateSource)
        vm.onIntent(SettingsIntent.SetUpdateChannel(UpdateChannel.STABLE))
        advanceUntilIdle()
        assertEquals(UpdateSource.MIRRORCHYAN, vm.uiState.first().update.updateSource)
        assertNull(vm.uiState.first().update.checkResult)
        assertNull(vm.uiState.first().update.errorPrompt)
    }

    @Test
    fun `channel change clears stale result and requires a new check before download`() = runTest {
        var resolved = false
        val service = mockk<UpdateService> {
            coEvery { check(any()) } returns UpdateCheckResult.UpdateAvailable(UpdateSource.GITHUB, UpdateInfo("2.0.0"))
            coEvery { resolve(any()) } coAnswers {
                resolved = true
                error("Stale update must not be resolved")
            }
        }
        val vm = viewModel(FakeAppSettingsGateway(), service)
        vm.onIntent(SettingsIntent.CheckUpdate)
        vm.onIntent(SettingsIntent.SetUpdateChannel(UpdateChannel.IQOO16))
        vm.onIntent(SettingsIntent.DownloadUpdate)
        advanceUntilIdle()
        assertFalse(resolved)
        assertNull(vm.uiState.first().update.updatePrompt)
    }

    @Test
    fun `selection cannot change while a check is in flight`() = runTest {
        val gate = CompletableDeferred<Unit>()
        val settings = FakeAppSettingsGateway()
        val vm = viewModel(settings, mockk {
            coEvery { check(any()) } coAnswers {
                gate.await()
                UpdateCheckResult.UpToDate(UpdateSource.MIRRORCHYAN, "3.12.6-iqoo.2185")
            }
        })
        vm.onIntent(SettingsIntent.CheckUpdate)
        vm.onIntent(SettingsIntent.SetUpdateChannel(UpdateChannel.IQOO16))
        vm.onIntent(SettingsIntent.SetUpdateSource(UpdateSource.GITHUB))
        assertEquals(UpdateChannel.STABLE, settings.updateChannel.value)
        assertEquals(UpdateSource.MIRRORCHYAN, settings.updateSource.value)
        gate.complete(Unit)
        advanceUntilIdle()
    }

    @Test
    fun `suspended persistence and delayed flow cannot expose a stale update`() = runTest {
        val persisted = FakeAppSettingsGateway()
        val writeGate = CompletableDeferred<Unit>()
        val settings = object : AppSettingsGateway by persisted {
            override suspend fun setUpdateChannel(channel: UpdateChannel) {
                writeGate.await()
                // A successful DataStore edit may return before its collector updates StateFlow.
            }
        }
        var checks = 0
        var resolves = 0
        val vm = viewModel(settings, mockk {
            coEvery { check(any()) } coAnswers {
                checks++
                UpdateCheckResult.UpdateAvailable(UpdateSource.GITHUB, UpdateInfo("3.12.6-iqoo.2186"))
            }
            coEvery { resolve(any()) } coAnswers {
                resolves++
                error("No download is allowed until the new selection is observed and checked")
            }
        })
        vm.onIntent(SettingsIntent.CheckUpdate)
        assertEquals(1, checks)
        vm.onIntent(SettingsIntent.SetUpdateChannel(UpdateChannel.IQOO16))
        assertTrue(vm.uiState.first().update.checking)
        assertNull(vm.uiState.first().update.checkResult)
        vm.onIntent(SettingsIntent.CheckUpdate)
        vm.onIntent(SettingsIntent.DownloadUpdate)
        assertEquals(1, checks)
        assertEquals(0, resolves)

        writeGate.complete(Unit)
        advanceUntilIdle()
        assertTrue(vm.uiState.first().update.checking)
        vm.onIntent(SettingsIntent.CheckUpdate)
        vm.onIntent(SettingsIntent.DownloadUpdate)
        assertEquals(1, checks)
        assertEquals(0, resolves)

        persisted.updateChannel.value = UpdateChannel.IQOO16
        advanceUntilIdle()
        assertFalse(vm.uiState.first().update.checking)
        assertNull(vm.uiState.first().update.checkResult)
        vm.onIntent(SettingsIntent.CheckUpdate)
        assertEquals(2, checks)
    }

    private fun viewModel(settings: AppSettingsGateway, service: UpdateService): SettingsViewModel {
        val definition = ProjectDefinition(
            name = "demo", version = "1", controllers = listOf(ControllerDefinition()),
            resources = emptyList(), tasks = emptyList(), groups = emptyList(), options = emptyMap(), templates = emptyList(),
            metadata = ProjectMetadata(githubRepository = "overflow65537/MAA_Punish", mirrorchyanRid = "upstream-rid"),
        )
        return SettingsViewModel(
            permissionGateway = FakePermissionGateway(), appSettings = settings,
            projectRepository = FakeProjectRepository(ProjectState.Ready(definition, emptyList())),
            updateService = service, updateDownloader = mockk(), apkInstaller = mockk<SystemApkInstaller>(),
            currentVersion = "3.12.6-iqoo.2185", abi = AndroidAbi.UNIVERSAL,
        )
    }
}
