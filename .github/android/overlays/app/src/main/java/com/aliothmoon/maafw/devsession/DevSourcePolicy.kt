package com.aliothmoon.maafw.devsession

import kotlinx.serialization.Serializable

@Serializable
data class DevSourceLimits(
    val max_file_bytes: Long,
    val max_files: Int,
    val max_total_bytes: Long,
    val max_manifest_bytes: Long,
)

@Serializable
data class DevSourcePolicySpec(
    val version: Int = 1,
    val files: List<String>,
    val roots: Map<String, List<String>>,
    val excluded_prefixes: List<String>,
    val excluded_segments: List<String>,
    val limits: DevSourceLimits,
)

/** Shared declarative allowlist is bundled in assets and read by the PC tool. */
class DevSourcePolicy(val spec: DevSourcePolicySpec) {
    fun requireAllowed(path: String) {
        require(path.isNotEmpty() && path.length <= 512 && path.none { it < ' ' || it >= '\uD800' && it <= '\uDFFF' }) { "invalid_path" }
        require('\\' !in path && ':' !in path && !path.startsWith('/') && !path.endsWith('/')) { "invalid_path" }
        val segments = path.split('/')
        require(segments.none { it.isEmpty() || it.startsWith('.') || it.lowercase() in spec.excluded_segments }) { "protected_path" }
        val lower = path.lowercase()
        require(spec.excluded_prefixes.none { lower.startsWith(it.lowercase()) }) { "protected_path" }
        val root = segments.first()
        val suffix = path.substringAfterLast('.', "").lowercase().let { ".$it" }
        require(path in spec.files || segments.size > 1 && suffix in spec.roots[root].orEmpty()) { "path_not_in_source_policy" }
    }

    fun requireFiles(files: List<DevSourceFile>) {
        require(files.isNotEmpty() && files.size <= spec.limits.max_files) { "file_count_limit" }
        require(files.map { it.path.lowercase() }.toSet().size == files.size) { "path_case_collision" }
        var total = 0L
        for (file in files) {
            requireAllowed(file.path)
            require(file.size in 0..spec.limits.max_file_bytes && devHash.matches(file.sha256)) { "invalid_file_metadata" }
            total = Math.addExact(total, file.size)
            require(total <= spec.limits.max_total_bytes) { "total_size_limit" }
        }
        require(files.any { it.path == "interface.json" }) { "interface_required" }
        val paths = files.map { it.path }.toSet()
        require(paths.none { path -> path.split('/').dropLast(1).runningFold("") { a, p -> if (a.isEmpty()) p else "$a/$p" }.any { it in paths } }) { "file_parent_collision" }
    }
}
