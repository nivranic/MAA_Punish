package com.aliothmoon.maafw.ui.home

import android.content.Context
import org.json.JSONObject
import java.io.File

/** pi/ 目录下热更新清单文件；由 MAA_Punish 仓库的热更新流程写入并回读校验。 */
private const val HOTFIX_MANIFEST = "pi/hotfix.manifest.json"

/**
 * 资源版本标签：基准 pi.version（与 versionCode 同源）+ 已应用热更新次数。
 * 例如 "2296+1" 表示基准 2296 之上打过 1 次热更新；无清单、无记录或解析
 * 失败时退回基准号。任何异常都不允许影响首页渲染。
 */
fun resourceVersionLabel(context: Context): String {
    val base = com.aliothmoon.maafw.BuildConfig.VERSION_CODE.toString()
    return try {
        val dir = context.getExternalFilesDir(null) ?: return base
        val file = File(dir, HOTFIX_MANIFEST)
        if (!file.isFile) return base
        val updates = JSONObject(file.readText()).optJSONArray("updates") ?: return base
        val count = updates.length()
        if (count > 0) "$base+$count" else base
    } catch (_: Exception) {
        base
    }
}
