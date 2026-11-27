"""D02 controller acknowledgements are not game redemption results."""

import json

import pytest
from maa.tasker import Tasker

from action.basics import RedmeeCode as redeem
from test_audit_purchase_native import MemoryController
from test_daily_activity_native import native_resource
from test_support.chinese_frame import chinese_frame
from test_support.daily_progress import register_daily_progress
from test_support.native_lifecycle import stop_and_wait_idle


CODE = "OFFLINE_SYNTHETIC_CODE"
ENTRY = "兑换码"
CONFIRM = "确认兑换"
FOCUS = "点击兑换码"
FAST = {"pre_delay": 0, "post_delay": 0, "timeout": 300, "rate_limit": 10, "on_error": []}


def synthetic_page(language, caption):
    zh_tw = language == "zh_TW"
    captions = [((766, 522), "請輸入兌換碼" if zh_tw else "请输入兑换码")]
    if caption != "blank":
        label = "兌換" if zh_tw else "兑换"
        if caption == "rules":
            label += "規則" if zh_tw else "规则"
        captions.append(((1084, 629), label))
    return chinese_frame(language, captions)


def install_resource(tmp_path, monkeypatch, language):
    monkeypatch.setattr(redeem, "HISTORY_PATH", tmp_path / "inv_code.json")
    resource = native_resource(tmp_path, language)
    register_daily_progress(resource, monkeypatch, tmp_path / "daily.json")
    assert resource.register_custom_action("RedeemCode", redeem.RedeemCode())
    assert resource.register_custom_recognition("CheckRedeemCode", redeem.CheckRedeemCode())
    return resource


def run_entry(resource, image, behavior, codes, at_submit=None):
    calls = []

    class Controller(MemoryController):
        def screencap(self):
            return image.copy()

        def click(self, x, y):
            kind = "submit" if x >= 1058 else "focus"
            calls.append(kind)
            if kind == "submit" and at_submit is not None:
                at_submit()
            return behavior != f"{kind}-nack"

        def input_text(self, text):
            calls.append(("text", text))
            return behavior != "text-nack"

    controller, tasker = Controller(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    overrides = {name: dict(FAST) for name in (
        ENTRY, "日常账本准入", "日常断点_兑换码", redeem.CHECK_NODE, redeem.CODE_NODE, FOCUS, CONFIRM,
    )}
    overrides[redeem.CODE_NODE].pop("on_error")  # Exercise production scoped failure.
    overrides[redeem.CODE_NODE]["action"] = {"param": {"custom_action_param": {"code": codes}}}
    # Keep the first two real native candidates; the fixture simulates an input
    # page, not profile navigation, generic rewards, or a business response.
    overrides[redeem.CHECK_NODE]["next"] = ["[JumpBack]可以输入兑换码"]
    try:
        job = tasker.post_task(ENTRY, overrides).wait()
        nodes = [tasker.get_node_detail(node_id) for node_id in job.get().node_id_list]
        assert nodes[1].name == "日常账本准入" and nodes[1].completed
        assert nodes[2].name == "日常断点_兑换码" and nodes[2].completed
        return calls, [(node.name, node.completed) for node in nodes]
    finally:
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("language", ["base", "zh_TW"])
@pytest.mark.parametrize("behavior,caption,expected", [
    ("focus-nack", "button", ["focus"]),
    ("text-nack", "button", ["focus", ("text", CODE)]),
    ("ordinary", "blank", ["focus", ("text", CODE)]),
    ("ordinary", "rules", ["focus", ("text", CODE)]),
])
def test_redeem_pre_submit_failure_leaves_code_unattempted(tmp_path, monkeypatch, language, behavior, caption, expected):
    resource = install_resource(tmp_path, monkeypatch, language)
    calls, nodes = run_entry(resource, synthetic_page(language, caption), behavior, [CODE])
    assert calls == expected
    assert (redeem.CODE_NODE, False) in nodes
    assert not (tmp_path / "inv_code.json").exists()
    assert not (tmp_path / "daily.json").exists()


@pytest.mark.parametrize("language", ["base", "zh_TW"])
@pytest.mark.parametrize("behavior", ["ordinary", "submit-nack"])
def test_redeem_unknown_submission_stays_unconfirmed_and_is_not_repeated(tmp_path, monkeypatch, language, behavior):
    resource = install_resource(tmp_path, monkeypatch, language)
    frame = synthetic_page(language, "button")
    state = tmp_path / "inv_code.json"
    observed = []
    calls, nodes = run_entry(resource, frame, behavior, [CODE],
                             at_submit=lambda: observed.append(json.loads(state.read_text(encoding="utf-8"))))
    assert calls == ["focus", ("text", CODE), "submit"]
    assert observed == [{"inv_code": [], "unconfirmed": [CODE]}]
    assert json.loads(state.read_text(encoding="utf-8")) == observed[0]
    assert (redeem.CODE_NODE, False) in nodes
    before = state.read_bytes()
    # Fresh native Tasker and controller, no reliance on the callback's memory.
    next_calls, next_nodes = run_entry(resource, frame, "ordinary", [CODE])
    assert next_calls == []
    assert ("空任务", True) in next_nodes
    assert state.read_bytes() == before
    assert not (tmp_path / "daily.json").exists()


@pytest.mark.parametrize("language", ["base", "zh_TW"])
@pytest.mark.parametrize("legacy", [False, True], ids=["empty-list", "legacy-cache"])
def test_redeem_no_pending_code_has_no_input(tmp_path, monkeypatch, language, legacy):
    state = tmp_path / "inv_code.json"
    if legacy:
        state.write_text(json.dumps({"inv_code": [CODE]}), encoding="utf-8")
    resource = install_resource(tmp_path, monkeypatch, language)
    calls, nodes = run_entry(resource, synthetic_page(language, "button"), "ordinary", [CODE] if legacy else [])
    assert not calls
    assert ("空任务", True) in nodes
    assert state.exists() is legacy
    if legacy:
        assert json.loads(state.read_text(encoding="utf-8")) == {"inv_code": [CODE]}


@pytest.mark.parametrize("language", ["base", "zh_TW"])
def test_redeem_refuses_confirmation_when_record_cannot_be_saved(tmp_path, monkeypatch, language):
    def fail_write(_):
        raise OSError("synthetic write failure")

    resource = install_resource(tmp_path, monkeypatch, language)
    monkeypatch.setattr(redeem, "_remember_unconfirmed", fail_write)
    calls, nodes = run_entry(resource, synthetic_page(language, "button"), "ordinary", [CODE])
    assert calls == ["focus", ("text", CODE)]
    assert (redeem.CODE_NODE, False) in nodes
    assert not (tmp_path / "inv_code.json").exists()
    assert not (tmp_path / "daily.json").exists()


@pytest.mark.parametrize("language", ["base", "zh_TW"])
def test_redeem_with_ambiguous_history_never_falls_back_to_empty(tmp_path, monkeypatch, language):
    resource = install_resource(tmp_path, monkeypatch, language)
    state = tmp_path / "inv_code.json"
    blob = f'{{"unconfirmed":["{CODE}"],"unconfirmed":[]}}\n'.encode()
    state.write_bytes(blob)
    calls, _ = run_entry(resource, synthetic_page(language, "button"), "ordinary", [CODE])
    assert not calls
    assert state.read_bytes() == blob


def record_warnings(monkeypatch):
    messages = []

    def warning(fmt, *args):
        messages.append(fmt % args if args else fmt)

    monkeypatch.setattr(redeem.logger, "warning", warning)
    return messages


@pytest.mark.parametrize("language", ["base", "zh_TW"])
def test_redeem_unreadable_history_is_reported_in_agent_log(tmp_path, monkeypatch, language):
    # 入口跳过仍然是「任务成功」，所以原因必须留在 agent 日志里，不能只 print 到 stdout。
    resource = install_resource(tmp_path, monkeypatch, language)
    (tmp_path / "inv_code.json").write_text("{not json", encoding="utf-8")
    messages = record_warnings(monkeypatch)
    calls, nodes = run_entry(resource, synthetic_page(language, "button"), "ordinary", [CODE])
    assert not calls
    assert ("空任务", True) in nodes
    assert any("history_unreadable" in message for message in messages)


@pytest.mark.parametrize("language", ["base", "zh_TW"])
def test_redeem_unconfirmed_pending_is_reported_in_agent_log(tmp_path, monkeypatch, language):
    resource = install_resource(tmp_path, monkeypatch, language)
    state = tmp_path / "inv_code.json"
    state.write_text(json.dumps({"inv_code": [], "unconfirmed": [CODE]}), encoding="utf-8")
    messages = record_warnings(monkeypatch)
    calls, nodes = run_entry(resource, synthetic_page(language, "button"), "ordinary", [CODE])
    assert not calls
    assert ("空任务", True) in nodes
    assert any("unconfirmed_pending" in message for message in messages)
