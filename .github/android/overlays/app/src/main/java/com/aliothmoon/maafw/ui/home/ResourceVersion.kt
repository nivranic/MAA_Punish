package com.aliothmoon.maafw.ui.home

import android.content.Context
import org.json.JSONObject
import java.io.File

/** pi/ 目录下热更新清单文件；由 MAA_Punish 仓库的热更新流程写入并回读校验。 */
private const val HOTFIX_MANIFEST = "pi/hotfix.manifest.json"

/**
 * 资源版本标签（游戏惯例：资源版本是独立自增的整数，与 App 版本号相互独立）。
 * 取热更新清单的 base_pi_version + 已应用热更新次数，即当前生效的资源版本：
 * 每次热更新 +1；从未热更时即 APK 自带的资源版本（BuildConfig.VERSION_CODE 同源）。
 * 无清单、无记录或解析失败时退回 APK 自带版本。任何异常都不允许影响首页渲染。
 */
fun resourceVersionLabel(context: Context): String {
    val bundled = com.aliothmoon.maafw.BuildConfig.VERSION_CODE.toString()
    return try {
        val dir = context.getExternalFilesDir(null) ?: return bundled
        val file = File(dir, HOTFIX_MANIFEST)
        if (!file.isFile) return bundled
        val manifest = JSONObject(file.readText())
        val base = manifest.optInt("base_pi_version", -1)
        if (base < 0) return bundled
        val updates = manifest.optJSONArray("updates")
        val count = updates?.length() ?: 0
        (base + count).toString()
    } catch (_: Exception) {
        bundled
    }
}
