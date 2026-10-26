"""Explicit screenshot output contracts, using synthetic images rather than a game."""

from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

import numpy as np
import pytest

from action.combat.capture import CombatCaptureError
from audit_config import AuditConfig
from test_support.fakes import FakeContext

screen = importlib.import_module("action.basics.ScreenShot")


@dataclass
class ScreenshotJob:
    image: object
    succeeded: bool = True
    error_at: str = ""
    error: Exception | None = None
    waited: bool = False
    get_calls: int = 0

    def wait(self):
        self.waited = True
        if self.error_at == "wait":
            raise self.error
        return self

    def get(self):
        assert self.waited
        self.get_calls += 1
        if self.error_at == "get":
            raise self.error
        return self.image


def image():
    return np.array([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8)


def run_screenshot(job, param='{"type":"RG4"}'):
    context = FakeContext()

    def post():
        if job.error_at == "post":
            raise job.error
        return job

    context.tasker.controller = SimpleNamespace(post_screencap=post)
    return screen.ScreenShot().run(context, SimpleNamespace(custom_action_param=param))


@pytest.fixture(autouse=True)
def output_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(screen, "AUDIT_CONFIG", AuditConfig())
    return tmp_path


def old_file(path, contents=b"old evidence"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    os.utime(path, (1, 1))
    return path


def test_failed_job_never_reads_cached_image_writes_or_cleans_history(output_directory):
    history = old_file(output_directory / "debug" / "history.png")
    job = ScreenshotJob(image(), succeeded=False)

    with pytest.raises(CombatCaptureError):
        run_screenshot(job)

    assert job.waited and job.get_calls == 0
    assert list(history.parent.iterdir()) == [history]
    assert history.read_bytes() == b"old evidence"


@pytest.mark.parametrize("bad", [None, np.zeros((0, 1, 3), dtype=np.uint8),
                                np.zeros((2, 2), dtype=np.uint8),
                                np.zeros((2, 2, 4), dtype=np.uint8),
                                np.zeros((2, 2, 3), dtype=np.float32)])
def test_invalid_current_image_cannot_create_output(bad, output_directory):
    with pytest.raises(CombatCaptureError):
        run_screenshot(ScreenshotJob(bad))
    assert not (output_directory / "debug").exists()


@pytest.mark.parametrize("stage", ["post", "wait", "get"])
def test_backend_error_preserves_original_exception_and_does_not_write(stage, output_directory):
    error = OSError("controller disconnected")
    with pytest.raises(OSError) as raised:
        run_screenshot(ScreenshotJob(image(), error_at=stage, error=error))
    assert raised.value is error
    assert not (output_directory / "debug").exists()


def test_valid_current_bgr_image_is_written_as_rgb_png(output_directory):
    result = run_screenshot(ScreenshotJob(image()))
    assert result.success is True
    paths = list((output_directory / "debug").glob("RG4_*.png"))
    assert len(paths) == 1
    raw = paths[0].read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    chunks = {}
    position = 8
    while position < len(raw):
        size = struct.unpack("!I", raw[position:position + 4])[0]
        kind = raw[position + 4:position + 8]
        data = raw[position + 8:position + 8 + size]
        crc = struct.unpack("!I", raw[position + 8 + size:position + 12 + size])[0]
        assert crc == zlib.crc32(kind + data)
        chunks[kind] = data
        position += 12 + size
    assert struct.unpack("!2I5B", chunks[b"IHDR"]) == (2, 1, 8, 2, 0, 0, 0)
    assert zlib.decompress(chunks[b"IDAT"]) == b"\x00\x03\x02\x01\x06\x05\x04"
    assert chunks[b"IEND"] == b""


def test_audit_output_is_session_owned_and_preserves_all_history(output_directory, monkeypatch):
    session = "a" * 32
    monkeypatch.setattr(screen, "AUDIT_CONFIG", AuditConfig(True, session, "b" * 32))
    directory = output_directory / "debug" / "audit" / session / "screenshots"
    histories = [old_file(output_directory / "debug" / "old.png"),
                 old_file(directory / "previous.png"),
                 old_file(output_directory / "debug" / "audit" / ("c" * 32) / "screenshots" / "old.png")]
    monkeypatch.setattr(screen.time, "time_ns", lambda: 123)

    assert run_screenshot(ScreenshotJob(image()), '{"type":"../../unsafe/path"}').success
    assert run_screenshot(ScreenshotJob(image()), '{"type":"../../unsafe/path"}').success

    assert all(path.read_bytes() == b"old evidence" for path in histories)
    outputs = set(output_directory.rglob("*.png")) - set(histories)
    assert len(outputs) == 2
    assert all(path.parent == directory for path in outputs)


def test_same_filename_is_rejected_without_overwriting_evidence(output_directory, monkeypatch):
    monkeypatch.setattr(screen, "AUDIT_CONFIG", AuditConfig(True, "a" * 32, "b" * 32))
    monkeypatch.setattr(screen.time, "time_ns", lambda: 123)
    monkeypatch.setattr(screen.time, "strftime", lambda _format: "fixed")
    monkeypatch.setattr(screen, "uuid4", lambda: SimpleNamespace(hex="c" * 32))
    run_screenshot(ScreenshotJob(image()))
    path = next(output_directory.rglob("*.png"))
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        run_screenshot(ScreenshotJob(image()))
    assert path.read_bytes() == original
    assert list(output_directory.rglob("*.png")) == [path]


def test_disabled_audit_configuration_restores_explicit_ordinary_output(output_directory, monkeypatch):
    monkeypatch.setattr(screen, "AUDIT_CONFIG", AuditConfig(True, "a" * 32, "b" * 32))
    run_screenshot(ScreenshotJob(image()))
    audit_outputs = set(output_directory.rglob("*.png"))
    monkeypatch.setattr(screen, "AUDIT_CONFIG", AuditConfig())
    run_screenshot(ScreenshotJob(image()))
    assert set((output_directory / "debug" / "audit").rglob("*.png")) == audit_outputs
    assert len(list((output_directory / "debug").glob("*.png"))) == 1


def test_ordinary_explicit_screenshot_retains_existing_retention_policy(output_directory):
    expired = old_file(output_directory / "debug" / "expired.png")
    user_data = old_file(output_directory / "debug" / "user.json")
    audit_history = old_file(output_directory / "debug" / "audit" / "history.png")
    assert run_screenshot(ScreenshotJob(image())).success
    assert not expired.exists()
    assert user_data.read_bytes() == audit_history.read_bytes() == b"old evidence"
