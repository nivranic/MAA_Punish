package com.aliothmoon.maafw.devsession

import android.content.Context
import com.aliothmoon.maafw.BuildConfig
import com.aliothmoon.maafw.RemoteService
import com.aliothmoon.maafw.config.UserConfigurationStore
import com.aliothmoon.maafw.domain.DiagnosticSeverity
import com.aliothmoon.maafw.domain.UserConfiguration
import com.aliothmoon.maafw.i18n.AppLocales
import com.aliothmoon.maafw.privileged.PrivilegedServicePort
import com.aliothmoon.maafw.project.DirectoryProjectSource
import com.aliothmoon.maafw.project.ProjectLoadResult
import com.aliothmoon.maafw.project.ProjectLoader
import com.aliothmoon.maafw.project.ProjectRepository
import com.aliothmoon.maafw.project.ProjectState
import com.aliothmoon.maafw.project.PiInstaller
import com.aliothmoon.maafw.runner.RunLauncher
import com.aliothmoon.maafw.runner.RunPlan
import com.aliothmoon.maafw.runner.RunPlanBuilder
import com.aliothmoon.maafw.runner.RunPlanPayload
import com.aliothmoon.maafw.runner.RunPlanResult
import com.aliothmoon.maafw.runner.runPlanWireJson
import com.aliothmoon.maafw.runner.runtimePayload
import com.aliothmoon.maafw.runner.withAuditLease
import com.aliothmoon.maafw.settings.AppSettingsManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import java.io.File
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import timber.log.Timber

/** USB requests only select a validated staged transaction; the app builds every runtime payload. */
class DevReloadCoordinator(
    private val context: Context,
    private val appScope: CoroutineScope,
    private val session: DevSessionCoordinator,
    private val launcher: RunLauncher,
    private val projectRepository: ProjectRepository,
    private val configurationStore: UserConfigurationStore,
    private val servicePort: PrivilegedServicePort,
    private val settings: AppSettingsManager,
    private val installer: PiInstaller,
) {
    private val mutableState = MutableStateFlow(DevReloadStatus())
    val state: StateFlow<DevReloadStatus> = mutableState.asStateFlow()
    private var job: Job? = null
    private var initialized = false
    private val cancelRequested = AtomicBoolean(false)
    private val root: File get() = requireNotNull(context.getExternalFilesDir(null))
    private val transaction: DevSourceTransaction by lazy {
        val spec = context.assets.open("dev-source-policy.json").bufferedReader(Charsets.UTF_8).use {
            devSessionJson.decodeFromString<DevSourcePolicySpec>(it.readText())
        }
        require(spec.version == 1) { "source_policy_version" }
        DevSourceTransaction(root, BuildConfig.APPLICATION_ID, BuildConfig.VERSION_CODE.toLong(), DevSourcePolicy(spec))
    }

    /** Recovery has no USB dependency and runs before any half-promoted project may be consumed. */
    @Synchronized
    fun setup() {
        if (initialized) return
        initialized = true
        // A new process has no valid old challenge/session. Journals are always retained.
        appScope.launch(Dispatchers.IO) {
            runCatching {
                transaction.discardAbandonedSessions(setOfNotNull(session.lease.value?.sessionId, mutableState.value.session_id.takeIf { job?.isActive == true }))
            }
        }
        appScope.launch(Dispatchers.IO) {
            var previous: String? = null
            session.lease.collect { lease ->
                if (previous != null && previous != lease?.sessionId) {
                    val retained = setOfNotNull(lease?.sessionId, mutableState.value.session_id.takeIf { job?.isActive == true })
                    runCatching { transaction.discardAbandonedSessions(retained) }
                }
                previous = lease?.sessionId
            }
        }
        val pending = runCatching { transaction.pending() }.getOrElse {
            DevReloadGuard.enter()
            mutableState.value = DevReloadStatus(phase = "blocked", reason = "recovery_storage_unavailable")
            return
        }
        if (pending.isEmpty()) return
        DevReloadGuard.enter()
        DevReloadGuard.recovering.set(true)
        job = appScope.launch(Dispatchers.IO) {
            val operation = UUID.randomUUID().toString().replace("-", "")
            mutableState.value = DevReloadStatus(operation_id = operation, phase = "recovering")
            try {
                withExclusiveMaintenance {
                        servicePort.useService { remote ->
                            awaitNative(remote, DevNativeRequest("begin", operation), "stopped")
                            pending.forEach { transaction.recoverForCurrentApk(it) { installer.ensureInstalled() } }
                            native(remote, DevNativeRequest("release", operation)).also { check(it.phase == "released") }
                        }
                }
                DevReloadGuard.recovering.set(false)
                DevReloadGuard.leave()
                projectRepository.reload()
                mutableState.value = DevReloadStatus(operation_id = operation, phase = "recovered")
            } catch (failure: Exception) {
                // Keep both UI/configuration guard and persistent journal; never auto-run an incomplete PI.
                Timber.w(failure, "Source recovery blocked")
                mutableState.value = DevReloadStatus(operation_id = operation, phase = "blocked", reason = "source_recovery_required")
            }
        }
    }

    @Synchronized
    fun submit(request: DevReloadRequest, lease: DevSessionLease): DevReloadStatus {
        val binding = session.binding.value
        if (request.version != 1 || request.`package` != BuildConfig.APPLICATION_ID ||
            binding == null || request.app_instance != binding.app_instance || request.challenge != binding.challenge ||
            request.host_id != lease.hostId || request.session_id != lease.sessionId || session.lease.value != lease ||
            !devToken.matches(request.request_id) || !devToken.matches(request.operation_id)
        ) return rejected(request, "reload_binding_invalid")
        val current = mutableState.value
        if (request.operation == "status") return current
        if (request.operation == "cancel") {
            if (request.operation_id != current.operation_id || request.session_id != current.session_id) return rejected(request, "operation_mismatch")
            cancelRequested.set(true)
            return current
        }
        if (request.operation != "apply" || !devHash.matches(request.revision) || !devHash.matches(request.manifest_sha256)) {
            return rejected(request, "invalid_reload_request")
        }
        if (request.operation_id == current.operation_id && request.session_id == current.session_id) return current
        if (job?.isActive == true || DevReloadGuard.busy.value) return rejected(request, "maintenance_busy")
        cancelRequested.set(false)
        DevReloadGuard.enter()
        mutableState.value = DevReloadStatus(session_id = request.session_id, operation_id = request.operation_id, request_id = request.request_id, phase = "validating", revision = request.revision)
        job = appScope.launch(Dispatchers.IO) { apply(request, lease) }
        return mutableState.value
    }

    private fun rejected(request: DevReloadRequest, reason: String) = DevReloadStatus(
        session_id = request.session_id, operation_id = request.operation_id, request_id = request.request_id,
        phase = "rejected", reason = reason,
    )

    private fun requireLease(lease: DevSessionLease) {
        check(!cancelRequested.get() && session.lease.value == lease) { "source_sync_cancelled_or_disconnected" }
    }

    private suspend fun apply(request: DevReloadRequest, lease: DevSessionLease) {
        var blocked = false
        var validated: DevSourceManifest? = null
        try {
            val manifest = transaction.validate(request.session_id, request.operation_id, request.revision, request.manifest_sha256)
            validated = manifest
            requireLease(lease)
            withExclusiveMaintenance {
                    val previous = projectRepository.state.value as? ProjectState.Ready ?: error("project_not_ready")
                    val config = configurationStore.data.first()
                    val oldPlan = build(previous.definition, config)
                    servicePort.useService { remote ->
                        mutableState.value = mutableState.value.copy(phase = "stopping")
                        var oldPayload: RunPlanPayload? = null
                        var promoted = false
                        var committed = false
                        try {
                            val snapshot = awaitNative(remote, DevNativeRequest("begin", request.operation_id), "stopped")
                            require(snapshot.safe_stopped && snapshot.prepared_payload.isNotEmpty()) { "display_not_previously_prepared" }
                            oldPayload = runPlanWireJson.decodeFromString<RunPlanPayload>(snapshot.prepared_payload)
                            requireLease(lease)
                            mutableState.value = mutableState.value.copy(phase = "promoting")
                            // From the first PI write onward disconnect/cancellation cannot abandon recovery.
                            withContext(NonCancellable) {
                                promoted = true
                                transaction.promote(manifest)
                                val loaded = ProjectLoader(DirectoryProjectSource(File(root, "pi"))).load()
                                require(loaded is ProjectLoadResult.Ready && loaded.diagnostics.none { it.severity == DiagnosticSeverity.Error }) { "candidate_project_invalid" }
                                val plan = build(loaded.definition, config)
                                require(plan.controller.name == oldPlan.controller.name &&
                                    plan.controller.type == oldPlan.controller.type &&
                                    plan.controller.displayShortSide == oldPlan.controller.displayShortSide &&
                                    plan.controller.displayLongSide == oldPlan.controller.displayLongSide &&
                                    plan.controller.displayRaw == oldPlan.controller.displayRaw
                                ) { "controller_definition_changed" }
                                val baseline = requireNotNull(oldPayload)
                                val payload = plan.runtimePayload(
                                    File(root, "pi"), baseline.screenWidth, baseline.screenHeight, baseline.displayMode,
                                    context.applicationInfo.sourceDir, context.applicationInfo.nativeLibraryDir,
                                    session.lease.value,
                                ).copy(tasks = emptyList())
                                mutableState.value = mutableState.value.copy(phase = "loading")
                                val ready = awaitNative(remote, DevNativeRequest("prepare", request.operation_id, 90_000, manifest.revision, runPlanWireJson.encodeToString(payload)), "prepared")
                                require(ready.controller_reused && ready.display_id == snapshot.display_id) { "display_or_controller_rebuilt" }
                                transaction.commit(manifest)
                                committed = true
                                projectRepository.publishMaintenance(loaded)
                                val released = native(remote, DevNativeRequest("release", request.operation_id))
                                require(released.phase == "released") { "maintenance_release_failed" }
                                transaction.finishCommit(manifest)
                                mutableState.value = mutableState.value.copy(
                                    phase = "loaded", revision = manifest.revision,
                                    native_generation = ready.native_generation, agent_generation = ready.agent_generation,
                                    display_id = ready.display_id, controller_reused = ready.controller_reused,
                                    resource_loaded = true, agent_loaded = plan.agents.isEmpty() || ready.agent_generation > 0,
                                )
                            }
                        } catch (failure: Exception) {
                            withContext(NonCancellable) {
                                try {
                                    check(!committed) { "committed_activation_ack_requires_recovery" }
                                    // A pending native call is not a rollback barrier. This BEGIN must actually finish.
                                    awaitNative(remote, DevNativeRequest("begin", request.operation_id), "stopped")
                                    if (promoted && transaction.hasJournal(manifest)) transaction.rollback(manifest)
                                    if (oldPayload != null) {
                                        val restored = awaitNative(remote, DevNativeRequest("prepare", request.operation_id, 90_000, manifest.base_revision.ifEmpty { "0".repeat(64) }, runPlanWireJson.encodeToString(requireNotNull(oldPayload).withAuditLease(session.lease.value))), "prepared")
                                        require(restored.controller_reused) { "rollback_controller_lost" }
                                    }
                                    native(remote, DevNativeRequest("release", request.operation_id)).also { check(it.phase == "released") }
                                    mutableState.value = mutableState.value.copy(phase = "failed", reason = failureCode(failure), revision = manifest.base_revision, rolled_back = promoted)
                                } catch (rollbackFailure: Exception) {
                                    blocked = true
                                    mutableState.value = mutableState.value.copy(phase = "blocked", reason = "native_or_source_recovery_required")
                                    Timber.w(rollbackFailure, "Source reload rollback blocked")
                                }
                            }
                            Timber.w(failure, "Source reload rejected or restored")
                        }
                    }
            }
        } catch (failure: Exception) {
            // Before native acceptance there were no source writes. A pending journal still wins over UI state.
            blocked = blocked || failure is DevMaintenanceBlockedException || DevReloadGuard.mustKeepBlocked { transaction.pending().isNotEmpty() }
            mutableState.value = mutableState.value.copy(phase = if (blocked) "blocked" else "failed", reason = if (blocked) "source_recovery_required" else failureCode(failure))
            Timber.w(failure, "USB source reload failed")
        } finally {
            // Validated but unpromoted uploads (including native-stop failures) do not accumulate.
            // Never remove a transaction which still owns its recovery journal.
            validated?.let { manifest ->
                runCatching { if (!transaction.hasJournal(manifest)) transaction.discardStaging(manifest) }
            }
            if (validated == null) runCatching { transaction.discardUnapplied(request.session_id, request.operation_id) }
            if (!blocked) DevReloadGuard.leave()
        }
    }

    private fun build(definition: com.aliothmoon.maafw.domain.ProjectDefinition, config: UserConfiguration): RunPlan =
        when (val built = RunPlanBuilder.build(definition, config, clientVersion = BuildConfig.VERSION_NAME, clientLanguage = AppLocales.currentProjectTag())) {
            is RunPlanResult.Success -> built.plan
            is RunPlanResult.Invalid -> error("active_configuration_invalid")
            RunPlanResult.NoExecutableTasks -> error("active_configuration_requires_enabled_task")
        }

    private suspend fun <T> withExclusiveMaintenance(block: suspend () -> T): T =
        DevReloadGuard.withSourceGate {
            launcher.withDevMaintenance {
                projectRepository.withSourceMaintenance { block() }
            }
        }

    private fun native(remote: RemoteService, request: DevNativeRequest): DevNativeStatus {
        val result = remote.devReload(devSessionJson.encodeToString(request)) ?: error("reload_api_unavailable")
        val status = devSessionJson.decodeFromString<DevNativeStatus>(result)
        require(status.operation_id == request.operation_id && status.phase != "rejected") { "native_reload_rejected_or_old_service" }
        return status
    }

    private suspend fun awaitNative(remote: RemoteService, request: DevNativeRequest, expected: String): DevNativeStatus {
        val deadline = System.nanoTime() / 1_000_000 + request.timeout_ms + 2_000
        var value = native(remote, request)
        while (value.phase in setOf("stopping", "preparing")) {
            check(System.nanoTime() / 1_000_000 < deadline) { "native_worker_still_active" }
            delay(50)
            value = native(remote, DevNativeRequest("poll", request.operation_id))
        }
        require(value.phase == expected) { value.reason.takeIf { it in safeFailureCodes } ?: "native_maintenance_failed" }
        return value
    }

    private fun failureCode(failure: Exception): String = failure.message?.takeIf { it in safeFailureCodes } ?: "source_validation_or_preparation_failed"

    private val safeFailureCodes = setOf(
        "display_not_previously_prepared", "controller_environment_changed", "display_environment_changed",
        "active_configuration_requires_enabled_task", "active_configuration_invalid", "project_not_ready",
        "controller_definition_changed", "display_or_controller_rebuilt", "candidate_project_invalid",
        "source_sync_cancelled_or_disconnected", "native_reload_rejected_or_old_service", "reload_api_unavailable",
        "native_worker_still_active", "stop_timeout_or_cancelled", "native_stop_failed", "native_maintenance_failed",
        "resource_or_agent_prepare_failed", "prepare_timeout_or_cancelled", "prepare_cancelled",
        "manifest_hash_mismatch", "manifest_apk_mismatch", "manifest_binding_mismatch", "revision_mismatch",
        "base_revision_mismatch", "source_file_size_mismatch", "source_file_hash_mismatch", "insufficient_space",
        "path_not_in_source_policy", "protected_path", "unmanaged_delete", "source_state_from_newer_apk",
    )
}
