"""Register real daily callbacks against an isolated test ledger."""

from action.basics import daily_progress as dp


def register_daily_progress(resource, monkeypatch, ledger):
    monkeypatch.setattr(dp, "ledger_path", lambda: ledger)
    assert resource.register_custom_action("DailyProgressAdmit", dp.DailyProgressAdmit())
    assert resource.register_custom_recognition("DailyProgressCheck", dp.DailyProgressCheck())
    assert resource.register_custom_action("DailyProgressMark", dp.DailyProgressMark())
    assert resource.register_custom_action("DailyProgressReset", dp.DailyProgressReset())
