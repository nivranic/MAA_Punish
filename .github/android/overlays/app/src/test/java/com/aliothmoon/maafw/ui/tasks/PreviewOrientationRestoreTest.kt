package com.aliothmoon.maafw.ui.tasks

import android.content.pm.ActivityInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PreviewOrientationRestoreTest {
    @Test
    fun `only portrait entry forces landscape`() {
        assertTrue(PreviewOrientationRestore.forceLandscapeOnEnter(enteredLandscape = false))
        assertFalse(PreviewOrientationRestore.forceLandscapeOnEnter(enteredLandscape = true))
    }

    @Test
    fun `exit from forced landscape restores portrait before giving control back`() {
        val (immediate, delayed) = PreviewOrientationRestore.exitSequence(
            enteredLandscape = false,
            original = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED,
        )

        assertEquals(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT, immediate)
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, delayed)
    }

    @Test
    fun `exit when user was already landscape keeps their orientation`() {
        val (immediate, delayed) = PreviewOrientationRestore.exitSequence(
            enteredLandscape = true,
            original = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED,
        )

        assertEquals(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, immediate)
        assertNull(delayed)
    }

    @Test
    fun `portrait original needs no delayed restore`() {
        val (immediate, delayed) = PreviewOrientationRestore.exitSequence(
            enteredLandscape = false,
            original = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT,
        )

        assertEquals(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT, immediate)
        assertNull(delayed)
    }
}
