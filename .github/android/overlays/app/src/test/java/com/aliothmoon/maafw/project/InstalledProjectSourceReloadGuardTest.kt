package com.aliothmoon.maafw.project

import com.aliothmoon.maafw.devsession.DevReloadGuard
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import kotlinx.coroutines.CancellationException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException

class InstalledProjectSourceReloadGuardTest {
    @get:Rule val temp = TemporaryFolder()

    private fun assertGuardFailurePropagates(error: Throwable) {
        val root = temp.newFolder()
        val installer = mockk<PiInstaller>()
        every { installer.installedDir() } returns root
        mockkObject(DevReloadGuard)
        try {
            every { DevReloadGuard.requireReadable(root) } throws error
            val thrown = assertThrows(error.javaClass) {
                InstalledProjectSource(installer).tryReadText("intro.md")
            }
            assertSame(error, thrown)
            // The blocked guard must fail before the lazy file delegate is initialized.
            verify(exactly = 1) { installer.installedDir() }
        } finally {
            unmockkObject(DevReloadGuard)
        }
    }

    @Test fun `recovery scan IO and permission failures cannot become optional text warnings`() {
        assertGuardFailurePropagates(IOException("recovery storage unavailable"))
        assertGuardFailurePropagates(SecurityException("recovery access denied"))
    }

    @Test fun `recovery cancellation and JVM errors propagate unchanged`() {
        assertGuardFailurePropagates(CancellationException("cancelled"))
        assertGuardFailurePropagates(AssertionError("broken recovery"))
    }

    @Test fun `a real pending recovery journal blocks an existing optional text file`() {
        val root = temp.newFolder("pi")
        File(root, "intro.md").writeText("must stay unread")
        File(temp.root, ".dev-session/reload/session/journal.json").apply {
            parentFile.mkdirs()
            writeText("pending")
        }
        val installer = mockk<PiInstaller>()
        every { installer.installedDir() } returns root
        val thrown = assertThrows(IllegalStateException::class.java) {
            InstalledProjectSource(installer).tryReadText("intro.md")
        }
        assertEquals("source_recovery_required", thrown.message)
        verify(exactly = 1) { installer.installedDir() }
    }
}
