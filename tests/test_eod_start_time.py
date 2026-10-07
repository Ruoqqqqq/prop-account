from datetime import time

import pandas as pd

from utils import eod_runner


def test_start_time_follows_us_dst():
    # US DST runs 8 Mar - 1 Nov 2026
    assert eod_runner.earliest_start(pd.Timestamp("2026-10-07 10:00")) == time(9, 30)
    assert eod_runner.earliest_start(pd.Timestamp("2026-12-07 10:00")) == time(10, 30)


def test_transition_weeks():
    # Singapore date can be ahead of the US date on the switchover days; check both sides.
    assert eod_runner.earliest_start(pd.Timestamp("2026-11-02 10:00")) == time(10, 30)
    assert eod_runner.earliest_start(pd.Timestamp("2026-03-09 10:00")) == time(9, 30)


def test_custom_times_from_config():
    cfg = {"eod_start_dst": "09:00", "eod_start_standard": "11:15"}
    assert eod_runner.earliest_start(pd.Timestamp("2026-07-01 10:00"), cfg) == time(9, 0)
    assert eod_runner.earliest_start(pd.Timestamp("2026-01-05 10:00"), cfg) == time(11, 15)


def test_too_early_skips_jasper(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(eod_runner.jasper_downloader, "download_reports", lambda **k: called.append(1) or (True, ""))
    monkeypatch.setattr(eod_runner.jasper_downloader, "load_config", lambda *a, **k: {})
    db = tmp_path / "t.db"
    winter_early = pd.Timestamp("2026-12-07 09:50")   # standard time -> starts 10:30
    r = eod_runner.run_eod_refresh(tmp_path, now=winter_early, db_path=db)
    assert r["status"] == "too_early" and not called
    summer_ok = pd.Timestamp("2026-07-07 09:50")      # DST -> starts 09:30, so it proceeds past the gate
    r = eod_runner.run_eod_refresh(tmp_path, now=summer_ok, db_path=db)
    assert r["status"] != "too_early"
