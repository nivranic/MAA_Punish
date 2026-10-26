package com.aliothmoon.maafw.project

import com.aliothmoon.maafw.MaaDispatchers
import com.aliothmoon.maafw.constant.AppPaths
import com.aliothmoon.maafw.devsession.DevReloadGuard
import io.mockk.every
import io.mockk.mockkObject
import io.mockk.unmockkObject
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.io.File
import java.io.InputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

@OptIn(ExperimentalCoroutinesApi::class)
class DevRecoveryInstallTest {
    @get:Rule val temp = TemporaryFolder()
    private val dispatcher = UnconfinedTestDispatcher()

    @Before fun before() {
        DevReloadGuard.recovering.set(false)
        DevReloadGuard.leave()
        mockkObject(MaaDispatchers)
        every { MaaDispatchers.IO } returns dispatcher
        mockkObject(AppPaths)
    }
    @After fun after() {
        DevReloadGuard.recovering.set(false)
        DevReloadGuard.leave()
        unmockkObject(MaaDispatchers)
        unmockkObject(AppPaths)
    }
    private fun pkg(text: String) = object : PiPackage {
        override fun manifest() = listOf("interface.json")
        override fun open(path: String): InputStream = ByteArrayInputStream(text.toByteArray())
    }
    private fun setupRoot(): File = temp.newFolder().also { every { AppPaths.ROOT } returns it }

    @Test fun recoveryWaitDoesNotLatchUnpackFailedOrOfferDestructiveRetry() = runTest(dispatcher) {
        val base = setupRoot()
        val installer = PiInstaller(pkg("{}"), 11)
        installer.ensureInstalled()
        val config = File(base, "pi/config/user.json").also { it.parentFile!!.mkdirs(); it.writeText("private") }
        val journal = File(base, ".dev-session/reload/${"a".repeat(32)}/${"b".repeat(32)}/journal.json")
            .also { it.parentFile!!.mkdirs(); it.writeText("pending") }
        DevReloadGuard.enter()
        DevReloadGuard.recovering.set(true)
        val coordinator = PiInstallCoordinator(installer)
        val waiting = async { coordinator.ensureInstalled() }
        runCurrent()
        assertFalse(waiting.isCompleted)
        assertEquals(PiInstallState.Checking, coordinator.state.value)
        assertEquals("private", config.readText())
        journal.delete()
        DevReloadGuard.recovering.set(false)
        DevReloadGuard.leave()
        assertTrue(waiting.await())
        assertEquals(PiInstallState.Ready, coordinator.state.value)
        assertEquals("private", config.readText())
    }

    @Test fun maintenanceWaitsForAnInstallerAlreadyInsideRealUnpack() = runTest {
        val base = setupRoot()
        every { MaaDispatchers.IO } returns Dispatchers.IO
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val blocking = object : PiPackage {
            override fun manifest() = listOf("interface.json")
            override fun open(path: String): InputStream {
                entered.countDown()
                check(release.await(5, TimeUnit.SECONDS)) { "test_unpack_timeout" }
                return ByteArrayInputStream("apk-source".toByteArray())
            }
        }
        val install = async(Dispatchers.IO) { PiInstallCoordinator(PiInstaller(blocking, 11)).ensureInstalled() }
        assertTrue(withContext(Dispatchers.IO) { entered.await(5, TimeUnit.SECONDS) })
        val maintenanceEntered = CompletableDeferred<Unit>()
        DevReloadGuard.enter()
        val maintenance = launch {
            DevReloadGuard.withSourceGate {
                assertEquals("11", File(base, "pi.version").readText())
                assertEquals("apk-source", File(base, "pi/interface.json").readText())
                maintenanceEntered.complete(Unit)
            }
        }
        try {
            delay(50)
            assertFalse(maintenanceEntered.isCompleted)
            assertFalse(File(base, "pi.version").exists())
        } finally {
            release.countDown()
        }
        assertTrue(install.await())
        maintenance.join()
        assertTrue(maintenanceEntered.isCompleted)
        DevReloadGuard.leave()
    }

    @Test fun installerCannotDeleteMarkerOrWritePiDuringMaintenance() = runTest(dispatcher) {
        val base = setupRoot()
        PiInstaller(pkg("old-apk"), 11).ensureInstalled()
        val config = File(base, "pi/config/user.json").also { it.parentFile!!.mkdirs(); it.writeText("private") }
        val entered = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        DevReloadGuard.enter()
        val maintenance = launch {
            DevReloadGuard.withSourceGate { entered.complete(Unit); release.await() }
        }
        entered.await()
        val coordinator = PiInstallCoordinator(PiInstaller(pkg("new-apk"), 12))
        val install = async { coordinator.ensureInstalled() }
        runCurrent()
        assertFalse(install.isCompleted)
        assertEquals("11", File(base, "pi.version").readText())
        assertEquals("old-apk", File(base, "pi/interface.json").readText())
        DevReloadGuard.leave()
        release.complete(Unit)
        maintenance.join()
        assertTrue(install.await())
        assertEquals(PiInstallState.Ready, coordinator.state.value)
        assertEquals("12", File(base, "pi.version").readText())
        assertEquals("private", config.readText())
    }
}
