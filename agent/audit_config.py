"""Process-start audit contract, resolved before any custom logger creates sinks."""

from dataclasses import dataclass
import os
import re
from uuid import uuid4

AUDIT_ENV_PREFIX = "MAA_PUNISH_AUDIT_"
AUDIT_LOG_MODE_ENV = AUDIT_ENV_PREFIX + "LOG_MODE"
AUDIT_SESSION_ID_ENV = AUDIT_ENV_PREFIX + "SESSION_ID"


@dataclass(frozen=True)
class AuditConfig:
    enabled: bool = False
    session_id: str | None = None
    stream_id: str | None = None


def _read_config() -> AuditConfig:
    reserved = {key for key in os.environ if key.startswith(AUDIT_ENV_PREFIX)}
    if not reserved:
        return AuditConfig()
    if reserved != {AUDIT_LOG_MODE_ENV, AUDIT_SESSION_ID_ENV}:
        raise RuntimeError("invalid audit environment: expected log mode and session ID only")
    if os.environ[AUDIT_LOG_MODE_ENV] != "stdout":
        raise RuntimeError("invalid audit log mode: expected stdout")
    session_id = os.environ[AUDIT_SESSION_ID_ENV]
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise RuntimeError("invalid audit session ID: expected 32 lowercase hexadecimal characters")
    return AuditConfig(True, session_id, uuid4().hex)


# Importing logger_component is already part of agent_file's imports. No late
# switch can turn a previously created FileHandler into an audit-only sink.
AUDIT_CONFIG = _read_config()
