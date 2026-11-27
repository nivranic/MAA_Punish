package com.aliothmoon.maafw.ui.tasks

import android.content.pm.ActivityInfo

/**
 * 全屏预览进出时的屏幕方向决策。
 *
 * 上游退出时只把 requestedOrientation 还原成进入前的值（窗体默认是 UNSPECIFIED），
 * 那只是解除「应用锁定方向」，不会把屏幕转回来：自动旋转开着、手机平放时系统保持
 * 最后一次方向，于是退出全屏后整块界面一直横着，要再强制一次竖屏才恢复。
 */
internal object PreviewOrientationRestore {
    /** 先强制竖屏，等系统应用完这次旋转再把方向控制权交还给进入前的设置 */
    const val RESTORE_DELAY_MS = 500L

    /** 进入全屏：原本不是横屏才强制横屏；已经是横屏时尊重用户当前方向 */
    fun forceLandscapeOnEnter(enteredLandscape: Boolean): Boolean = !enteredLandscape

    /**
     * 退出全屏要立即设置的方向，以及稍后要恢复的方向（null 表示不必再改）。
     *
     * 原本就是横屏时原样还回去；原本竖屏时必须先转回竖屏——只还原 UNSPECIFIED 不会生效。
     */
    fun exitSequence(enteredLandscape: Boolean, original: Int): Pair<Int, Int?> = when {
        enteredLandscape -> original to null
        original == ActivityInfo.SCREEN_ORIENTATION_PORTRAIT -> original to null
        else -> ActivityInfo.SCREEN_ORIENTATION_PORTRAIT to original
    }
}
