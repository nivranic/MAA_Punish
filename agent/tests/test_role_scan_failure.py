"""Failed or cancelled scans must preserve the last usable role cache."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from action.basics.RoleSelection import RoleSelection
from test_support.fakes import FakeContext


@pytest.fixture
def scan(tmp_path, monkeypatch):
    path = tmp_path / "role_cache.json"
    previous = {"focus": {"囚影": {"cage": 2}}, "main_update_at": 1}
    path.write_text(json.dumps(previous), encoding="utf-8")
    original = path.read_bytes()
    selector = RoleSelection()
    monkeypatch.setattr(selector, "_cache_path", lambda: path)
    monkeypatch.setattr(selector, "send_msg", Mock())
    monkeypatch.setattr(selector, "save_screenshot", Mock(return_value=True))
    context = FakeContext(node_data={"scan": {"attach": {"max_try": 1}}})
    argv = SimpleNamespace(
        node_name="scan", custom_action_param='{"selection_mode":"cache_refresh"}'
    )
    return selector, context, argv, path, original


@pytest.mark.parametrize("mode", ["cache_refresh", "standard"])
def test_empty_scan_fails_without_replacing_cache(scan, monkeypatch, mode):
    selector, context, argv, path, original = scan
    argv.custom_action_param = json.dumps({"selection_mode": mode})
    monkeypatch.setattr(selector, "recognize_role", lambda *args: {})

    result = selector.run(context, argv)

    assert not result.success
    assert path.read_bytes() == original
    selector.save_screenshot.assert_not_called()
    assert "保留原缓存" in selector.send_msg.call_args.args[1]


@pytest.mark.parametrize("partial", [{}, {"囚影": {"cage": 1}}])
def test_stop_during_page_scan_does_not_save_partial_cache(scan, monkeypatch, partial):
    selector, context, argv, path, original = scan

    def stop_during_scan(*args):
        context.tasker.stopping = True
        return partial

    monkeypatch.setattr(selector, "recognize_role", stop_during_scan)

    selector.run(context, argv)

    assert path.read_bytes() == original
    assert context.actions == []
    selector.save_screenshot.assert_not_called()
    selector.send_msg.assert_not_called()


def test_stop_during_last_swipe_does_not_save_cache(scan, monkeypatch):
    selector, context, argv, path, original = scan
    monkeypatch.setattr(selector, "recognize_role", lambda *args: {"囚影": {"cage": 1}})

    def stop_during_swipe(*args):
        context.tasker.stopping = True

    monkeypatch.setattr(context, "run_action", stop_during_swipe)

    selector.run(context, argv)

    assert path.read_bytes() == original


def test_completed_scan_updates_cache(scan, monkeypatch):
    selector, context, argv, path, _ = scan
    monkeypatch.setattr(selector, "recognize_role", lambda *args: {"囚影": {"cage": 1}})

    assert selector.run(context, argv).success

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["focus"] == {"囚影": {"cage": 3}}
    assert updated["main_update_at"] > 1
    selector.save_screenshot.assert_not_called()


def test_empty_cache_write_preserves_existing_file(scan):
    selector, _, _, path, original = scan

    selector.save_cache({})

    assert path.read_bytes() == original
