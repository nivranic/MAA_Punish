import logging
import itertools
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from dataclasses import dataclass

from audit_config import AUDIT_CONFIG

LOG_DIR = "debug"
RETENTION_DAYS = 3
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
_AUDIT_LOCK = threading.RLock()
_AUDIT_SEQUENCE = itertools.count(1)


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
            timestamp = datetime.now().strftime("%Y%m%d")
            log_file_path = os.path.join(LOG_DIR, f"custom_{timestamp}.log")
            file_handler = logging.FileHandler(log_file_path, mode="a", encoding="utf-8")
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
        if AUDIT_CONFIG.enabled:
            return
        if not os.path.isdir(LOG_DIR):
            return

        threshold = datetime.now() - timedelta(days=RETENTION_DAYS)
        for root, _, files in os.walk(LOG_DIR):
            for file_name in files:
                if not (file_name.startswith("custom_") and file_name.endswith(".log")):
                    continue
                try:
                    timestamp_str = file_name.split("_")[1].split(".")[0]
                    file_date = datetime.strptime(timestamp_str, "%Y%m%d")
                except Exception as exc:
                    self.logger.error(f"处理文件 {file_name} 时出错: {exc}")
                    continue
                if file_date < threshold:
                    file_path = os.path.join(root, file_name)
                    try:
                        os.remove(file_path)
                    except Exception as exc:
                        self.logger.error(
                            f"删除过期日志文件失败: {file_path} - {exc}"
                        )
                    else:
                        self.logger.info(f"已删除过期日志文件: {file_path}")

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
