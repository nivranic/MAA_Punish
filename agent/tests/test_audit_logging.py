"""Process-start sink tests; stdout capture does not prove Android logcat delivery."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1]
PREFIX = "MAA_PUNISH_AUDIT_"
MODE = PREFIX + "LOG_MODE"
SESSION = PREFIX + "SESSION_ID"
SESSION_ID = "a" * 32


def run_agent_script(directory, script, settings=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith(PREFIX)}
    environment.update(PYTHONPATH=str(AGENT_ROOT), PYTHONUTF8="1")
    environment.update(settings or {})
    return subprocess.run(
        [sys.executable, "-c", script], cwd=directory, env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=15, check=False,
    )


def audit_settings():
    return {MODE: "stdout", SESSION: SESSION_ID}


def historical_log(directory):
    old = directory / "debug" / "custom_20000101.log"
    old.parent.mkdir()
    old.write_text("retain audit history\n", encoding="utf-8")
    return old


def test_audit_initialization_creates_no_directory_and_escapes_multiline(tmp_path):
    result = run_agent_script(tmp_path, '''
from logger_component import LoggerComponent
component = LoggerComponent("early_import")
component.logger.info("first\\nsecond")
''', audit_settings())
    assert result.returncode == 0, result.stderr
    event, = [json.loads(line) for line in result.stdout.splitlines()]
    assert event["session_id"] == SESSION_ID
    assert event["sequence"] == 1
    assert event["message"] == "first\nsecond"
    assert event["monotonic_ns"] > 0
    assert list(tmp_path.iterdir()) == []


def test_audit_does_not_delete_existing_logs_even_on_explicit_cleanup(tmp_path):
    old = historical_log(tmp_path)
    result = run_agent_script(tmp_path, '''
from logger_component import LoggerComponent
component = LoggerComponent("history")
component._clear_old_logs()
component.logger.warning("preserved")
''', audit_settings())
    assert result.returncode == 0, result.stderr
    assert old.read_text(encoding="utf-8") == "retain audit history\n"
    assert list(old.parent.iterdir()) == [old]


@pytest.mark.parametrize("settings", [
    {MODE: "stdout"}, {SESSION: SESSION_ID},
    {MODE: "file", SESSION: SESSION_ID}, {MODE: "", SESSION: SESSION_ID},
    {MODE: "stdout", SESSION: "../escape"}, {MODE: "stdout", SESSION: "A" * 32},
    {MODE: "stdout", SESSION: SESSION_ID, PREFIX + "EXTRA": "1"},
])
def test_invalid_contract_fails_before_sinks_or_history_cleanup(tmp_path, settings):
    old = historical_log(tmp_path)
    result = run_agent_script(tmp_path, 'from logger_component import LoggerComponent', settings)
    assert result.returncode != 0
    assert "invalid audit" in result.stderr
    assert old.read_text(encoding="utf-8") == "retain audit history\n"
    assert list(old.parent.iterdir()) == [old]


def test_audit_sequence_matches_physical_stdout_across_threads_and_loggers(tmp_path):
    result = run_agent_script(tmp_path, '''
from concurrent.futures import ThreadPoolExecutor
from logger_component import LoggerComponent
components = [LoggerComponent(str(index)) for index in range(4)]
def emit(index):
    for message in range(25):
        components[index].logger.info("%s:%s", index, message)
with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(emit, range(4)))
try:
    raise ValueError("test error")
except ValueError:
    components[0].logger.exception("exception")
''', audit_settings())
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["sequence"] for event in events] == list(range(1, 102))
    assert [event["monotonic_ns"] for event in events] == sorted(event["monotonic_ns"] for event in events)
    assert len({event["stream_id"] for event in events}) == 1
    assert "ValueError: test error" in events[-1]["exception"]


def test_restart_has_new_stream_and_normal_restart_returns_to_file_logging(tmp_path):
    script = 'from logger_component import LoggerComponent; c = LoggerComponent("restart"); c.logger.info("hello")'
    first = run_agent_script(tmp_path, script, audit_settings())
    second = run_agent_script(tmp_path, script, audit_settings())
    assert first.returncode == second.returncode == 0
    a, b = json.loads(first.stdout), json.loads(second.stdout)
    assert a["stream_id"] != b["stream_id"]
    assert a["sequence"] == b["sequence"] == 1
    assert not (tmp_path / "debug").exists()
    normal = run_agent_script(tmp_path, script)
    assert normal.returncode == 0, normal.stderr
    assert "INFO - hello" in normal.stdout
    assert "maa_punish_audit" not in normal.stdout
    logs = list((tmp_path / "debug").glob("custom_*.log"))
    assert len(logs) == 1
    assert "INFO - hello" in logs[0].read_text(encoding="utf-8")


def test_configuration_is_fixed_at_import_before_any_logger(tmp_path):
    result = run_agent_script(tmp_path, '''
import os
from audit_config import AUDIT_CONFIG
os.environ.pop("MAA_PUNISH_AUDIT_LOG_MODE")
os.environ.pop("MAA_PUNISH_AUDIT_SESSION_ID")
from logger_component import LoggerComponent
component = LoggerComponent("fixed")
component.logger.info("still audit")
''', audit_settings())
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["session_id"] == SESSION_ID
    assert not (tmp_path / "debug").exists()


def test_owned_replacement_closes_file_and_preserves_foreign_handler(tmp_path):
    result = run_agent_script(tmp_path, '''
import logging
from logger_component import LoggerComponent
class ForeignHandler(logging.Handler):
    closed_by_component = False
    def emit(self, record): pass
    def close(self):
        self.closed_by_component = True
        super().close()
foreign = ForeignHandler()
logger = logging.getLogger("agent.owner")
logger.addHandler(foreign)
first = LoggerComponent("owner")
file_handler = next(h for h in first._handlers if isinstance(h, logging.FileHandler))
second = LoggerComponent("owner")
assert file_handler.stream is None
assert len(logger.handlers) == 3
first.close()
assert len(logger.handlers) == 3
assert foreign in logger.handlers and not foreign.closed_by_component
second.close()
assert logger.handlers == [foreign]
assert not foreign.closed_by_component
''')
    assert result.returncode == 0, result.stderr


def test_normal_mode_keeps_existing_retention_behavior(tmp_path):
    old = historical_log(tmp_path)
    result = run_agent_script(tmp_path, '''
from logger_component import LoggerComponent
component = LoggerComponent("normal")
component.logger.info("ordinary")
''')
    assert result.returncode == 0, result.stderr
    assert not old.exists()
    assert "INFO - ordinary" in result.stdout
    assert len(list(old.parent.glob("custom_*.log"))) == 1


def test_structured_emit_receipt_names_its_exact_sequence_among_ordinary_logs(tmp_path):
    result = run_agent_script(tmp_path, '''
from logger_component import LoggerComponent
component = LoggerComponent("receipt")
component.logger.info("ordinary before")
receipt = component.emit_audit({"audit_event": "prepare_commit", "value": 7})
component.logger.info("receipt=%s:%s", receipt.stream_id, receipt.sequence)
assert receipt.sequence == 2
''', audit_settings())
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert json.loads(events[1]["message"])["audit_event"] == "prepare_commit"
    assert events[2]["message"] == "receipt=" + events[1]["stream_id"] + ":2"


def test_structured_emit_returns_no_receipt_when_stdout_write_fails(tmp_path):
    result = run_agent_script(tmp_path, '''
import sys
from logger_component import LoggerComponent
class Broken:
    def write(self, value): raise OSError("broken pipe")
    def flush(self): pass
sys.stdout = Broken()
component = LoggerComponent("broken")
try:
    component.emit_audit({"audit_event": "prepare_commit"})
except RuntimeError as error:
    assert "emission failed" in str(error)
else:
    raise AssertionError("returned an ACK binding after broken stdout")
''', audit_settings())
    assert result.returncode == 0, result.stderr
