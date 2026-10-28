"""Post-return maintenance observations, composed after the canonical recovery patch."""

KOTLIN = "app/src/main/java/com/aliothmoon/maafw/"

PURCHASE_PATCHES = (
    (
        KOTLIN + "runner/MaaFrameworkRunnerPort.kt",
        '        fun onTaskFinished(taskName: String?, success: Boolean, message: String?, cancelled: Boolean) {\n',
        '''        fun onTaskFinished(taskName: String?, success: Boolean, message: String?, cancelled: Boolean) {
            if (cancelled) maintenanceCancelled = true
''',
    ),
)
