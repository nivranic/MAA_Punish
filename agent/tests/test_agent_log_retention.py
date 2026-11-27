"""Normal Agent logger retention; no sleeps, real device, or real log files."""

import pytest

from test_audit_logging import run_agent_script


def test_resident_logger_never_refreshes_an_old_bucket(tmp_path):
    result = run_agent_script(tmp_path, '''
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import logger_component as lc

# Freeze both the new clock and the original construction-time clock.
start = datetime(2020, 3, 1, 11, 59, tzinfo=timezone.utc)
clock = [start]
elapsed = [100.0]
class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return clock[0].astimezone(tz) if tz is not None else clock[0].replace(tzinfo=None)
lc.datetime = Clock
lc.time.time = lambda: clock[0].timestamp()
lc.time.monotonic = lambda: elapsed[0]

a = lc.LoggerComponent("resident-a")
b = lc.LoggerComponent("resident-b")
root = Path("debug")
a.logger.info("first bucket")
old, = root.glob("custom_*.log")
when = start.timestamp()
os.utime(old, (when, when))
clock[0] += timedelta(minutes=2)
elapsed[0] += 120
b.logger.info("next time bucket")
assert old.read_text(encoding="utf-8").count("bucket") == 1, "active agent refreshed an earlier bucket"
assert old.stat().st_mtime == when
assert any("next time bucket" in f.read_text(encoding="utf-8") for f in root.glob("custom_*.log") if f != old)

clock[0] += timedelta(days=8)
elapsed[0] += 8 * 86400
b.logger.warning("later bucket")
a.logger.info("other logger follows rotation")
assert not old.exists()
files = list(root.glob("custom_*.log"))
assert len(files) == 1
text = files[0].read_text(encoding="utf-8")
assert "later bucket" in text and "other logger follows rotation" in text
assert all(h.stream is None for c in (a,b) for h in c._handlers if isinstance(h, __import__("logging").FileHandler))
''')
    assert result.returncode == 0, result.stderr


def test_sweep_matches_exact_owned_top_level_names_and_ignores_mtime(tmp_path):
    root = tmp_path / "debug"
    root.mkdir()
    old = root / "custom_20000101.log"
    old.write_text("old but recently touched", encoding="utf-8")
    kept = (root / "custom_20000101_extra.log", root / "agent_cache.json",
            root / "nested" / "custom_20000101.log")
    kept[-1].parent.mkdir()
    for file in kept:
        file.write_text("outside log contract", encoding="utf-8")
    result = run_agent_script(tmp_path, '''
from logger_component import LoggerComponent
sink = LoggerComponent("owned-only")
sink.logger.info("new line")
''')
    assert result.returncode == 0, result.stderr
    assert not old.exists()
    assert all(file.read_text(encoding="utf-8") == "outside log contract" for file in kept)


def test_a_delayed_record_reaches_stdout_but_not_the_new_file_bucket(tmp_path):
    result = run_agent_script(tmp_path, '''
from pathlib import Path
from logger_component import LoggerComponent
sink = LoggerComponent("delayed")
l = sink.logger
r = l.makeRecord(l.name, 20, "", 0, "stale record", (), None)
r.created -= 8 * 24 * 3600
l.handle(r)
l.info("confirmed current record")
file, = Path("debug").glob("custom_*.log")
contents = file.read_text(encoding="utf-8")
assert "confirmed current record" in contents and "stale record" not in contents
''')
    assert result.returncode == 0, result.stderr
    assert "stale record" in result.stdout


def test_old_owned_name_does_not_follow_or_unlink_a_symlink(tmp_path):
    outside = tmp_path / "outside.log"
    outside.write_text("keep me", encoding="utf-8")
    root = tmp_path / "debug"
    root.mkdir()
    alias = root / "custom_20000101.log"
    try:
        alias.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable to this process")
    result = run_agent_script(tmp_path, '''
from logger_component import LoggerComponent
sink = LoggerComponent("skip-links")
sink.logger.info("current")
''')
    assert result.returncode == 0, result.stderr
    assert alias.is_symlink() and outside.read_text(encoding="utf-8") == "keep me"
