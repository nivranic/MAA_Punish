import logging
import itertools
import json
import os
import re
import stat
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from audit_config import AUDIT_CONFIG

LOG_DIR = "debug"
RETENTION_DAYS = 3
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
_AUDIT_LOCK = threading.RLock()
_AUDIT_SEQUENCE = itertools.count(1)
_FILE_LOCK = threading.RLock()
_LAST_SWEEP: dict[Path, float] = {}
_SWEEP_INTERVAL = 3600
_OWNED_NAME = re.compile(r"custom_([0-9]{8})(?:_(00|12))?\.log")


def _prune_ordinary_logs(directory: Path, wall_now: float) -> None:
    """Sweep exact top-level producer names at startup and during continued writes."""
    with _FILE_LOCK:
        elapsed = time.monotonic()
        previous = _LAST_SWEEP.get(directory)
        if previous is not None and 0 <= elapsed - previous < _SWEEP_INTERVAL:
            return
        _LAST_SWEEP[directory] = elapsed
        if directory.is_symlink() or directory.is_junction():
            return
        retention = timedelta(days=RETENTION_DAYS)
        utc_cutoff = datetime.fromtimestamp(wall_now, timezone.utc) - retention
        local_cutoff = datetime.fromtimestamp(wall_now) - retention
        failures = 0
        try:
            for path in directory.iterdir():
                match = _OWNED_NAME.fullmatch(path.name)
                if match is None:
                    continue
                try:
                    date = datetime.strptime(match[1], "%Y%m%d")
                    hour = match[2]
                    expired = (date < local_cutoff if hour is None else
                               date.replace(hour=int(hour), tzinfo=timezone.utc) <= utc_cutoff)
                    if expired and stat.S_ISREG(path.lstat().st_mode):
                        path.unlink()
                except (FileNotFoundError, ValueError):
                    continue  # concurrent cleanup, or a name that is not a real date
                except OSError:
                    failures += 1
        except FileNotFoundError:
            return
        except OSError:
            failures += 1
        if failures:
            # Do not recursively write to the sink whose filesystem just failed.
            print(f"[AgentLog] 无法清理 {failures} 项日志，稍后重试", file=sys.stderr)


class _OrdinaryFileHandler(logging.FileHandler):
    """A half-day UTC segment per emission; no file descriptor stays open while idle."""

    def __init__(self, directory: Path):
        self.directory = directory.absolute()
        super().__init__(self.directory / "custom_not_opened.log", mode="a", encoding="utf-8", delay=True)

    def _open(self):
        path = Path(self.baseFilename)
        if self.directory.is_symlink() or self.directory.is_junction() or path.is_symlink():
            raise OSError("refusing a linked Agent log path")

        def open_regular(name, flags):
            descriptor = os.open(name, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o666)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise OSError("refusing a non-regular Agent log")
                return descriptor
            except BaseException:
                os.close(descriptor)
                raise

        return open(path, self.mode, encoding=self.encoding, errors=self.errors, opener=open_regular)

    def emit(self, record):
        if self._closed:
            return
        with _FILE_LOCK:
            now = time.time()
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                _prune_ordinary_logs(self.directory, now)
                if not 0 <= now - record.created < _SWEEP_INTERVAL:
                    return  # a delayed record is still eligible for the independent stdout sink
                current = datetime.fromtimestamp(now, timezone.utc)
                hour = (current.hour // 12) * 12
                self.baseFilename = str(self.directory / f"custom_{current:%Y%m%d}_{hour:02d}.log")
                super().emit(record)
            except OSError:
                self.handleError(record)
            finally:
                if self.stream is not None:
                    stream, self.stream = self.stream, None
                    try:
                        stream.close()
                    except OSError:
                        self.handleError(record)


@dataclass(frozen=True)
class AuditReceipt:
    stream_id: str
    sequence: int


class _AuditStreamHandler(logging.StreamHandler):
    def handleError(self, record):
        # A commit consumer must never obtain a receipt for a failed stdout write.
        raise RuntimeError("audit stdout emission failed") from sys.exc_info()[1]


class _AuditFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {
            "event": "maa_punish_audit",
            "session_id": AUDIT_CONFIG.session_id,
            "stream_id": AUDIT_CONFIG.stream_id,
            "sequence": next(_AUDIT_SEQUENCE),
            "monotonic_ns": time.monotonic_ns(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        record._maa_audit_receipt = AuditReceipt(event["stream_id"], event["sequence"])
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


class LoggerComponent:
    """提供统一的自定义日志记录器配置与过期日志清理功能。"""

    def __init__(self, name: str):
        self._handlers = []
        self.logger = self._setup_logger(name)
        if not AUDIT_CONFIG.enabled:
            self._clear_old_logs()

    def __del__(self):
        self.close()

    def _setup_logger(self, name: str) -> logging.Logger:
        logger = logging.getLogger(f"agent.{name}")
        logger.setLevel(logging.DEBUG)
        for handler in logger.handlers[:]:
            if getattr(handler, "_maa_punish_owned", False):
                logger.removeHandler(handler)
                handler.close()
        logger.propagate = False

        if not AUDIT_CONFIG.enabled:
            os.makedirs(LOG_DIR, exist_ok=True)
            file_handler = _OrdinaryFileHandler(Path(LOG_DIR))
            file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            self._add_handler(logger, file_handler)

        stream_handler = (_AuditStreamHandler if AUDIT_CONFIG.enabled else logging.StreamHandler)(stream=sys.stdout)
        if AUDIT_CONFIG.enabled:
            # Every component shares the emit lock, so sequence and physical
            # stdout line order agree even when different threads/loggers emit.
            stream_handler.lock = _AUDIT_LOCK
            stream_handler.setFormatter(_AuditFormatter())
        else:
            stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        self._add_handler(logger, stream_handler)
        return logger

    def emit_audit(self, event: dict) -> AuditReceipt:
        """Emit on the existing ordered stream and return this event's binding."""
        if not AUDIT_CONFIG.enabled:
            raise RuntimeError("structured audit emission requires audit mode")
        message = json.dumps(event, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        record = self.logger.makeRecord(self.logger.name, logging.INFO, "", 0,
                                        message, (), None)
        with _AUDIT_LOCK:
            self.logger.handle(record)
            receipt = getattr(record, "_maa_audit_receipt", None)
            if receipt is None:
                raise RuntimeError("audit stream did not emit this event")
            return receipt

    def _add_handler(self, logger: logging.Logger, handler: logging.Handler):
        handler.setLevel(logging.DEBUG)
        handler._maa_punish_owned = True
        logger.addHandler(handler)
        self._handlers.append(handler)

    def _clear_old_logs(self):
        if not AUDIT_CONFIG.enabled:
            for handler in self._handlers:
                if isinstance(handler, _OrdinaryFileHandler):
                    _prune_ordinary_logs(handler.directory, time.time())

    def close(self):
        if not getattr(self, "logger", None):
            return
        for handler in self._handlers[:]:
            try:
                handler.close()
            except Exception:
                pass
            finally:
                self.logger.removeHandler(handler)
        self._handlers.clear()
