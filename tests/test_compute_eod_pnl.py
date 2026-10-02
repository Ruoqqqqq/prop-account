import pandas as pd
import pytest

from utils.archive_db import compute_eod_pnl


def _eod(rows):
    return pd.DataFrame(rows, columns=["client_no", "report_date", "total_equity"])


def _live(client_no="X", equity=160.0):
    return pd.DataFrame({"client_no": [client_no], "equity_bal": [equity]})


def _run(eod, now, adj=None, live=None):
    out = compute_eod_pnl(eod, live if live is not None else _live(), pd.Timestamp(now), adjustments=adj)
    return out.set_index("client_no").loc["X"]


# Sep 29/30 EOD, adjustment +30 booked in October.
SEP = [("X", "20260929", 100.0), ("X", "20260930", 110.0)]
OCT1 = SEP + [("X", "20261001", 150.0)]
OCT2 = OCT1 + [("X", "20261002", 155.0)]


def test_empty_history_returns_empty_frame():
    out = compute_eod_pnl(_eod([]), _live(), pd.Timestamp("2026-10-01"))
    assert out.empty


def test_no_adjustment_plain_differences():
    r = _run(_eod(SEP), "2026-10-01")
    assert r.intraday_pnl == 50      # 160 - 110
    assert r.daily_pnl_prev_day == 10  # 110 - 100
    assert r.monthly_pnl == 50


def test_first_trading_day_intraday_is_net_of_adjustment():
    r = _run(_eod(SEP), "2026-10-01", adj={"X": 30})
    assert r.intraday_pnl == 20
    assert r.daily_pnl_prev_day == 10  # Sep 30 vs Sep 29: unaffected
    assert r.monthly_pnl == 20


def test_second_trading_day_prev_day_is_net_of_adjustment():
    r = _run(_eod(OCT1), "2026-10-02", adj={"X": 30})
    assert r.daily_pnl_prev_day == 10  # 150 - 110 - 30
    assert r.intraday_pnl == 10        # 160 - 150, adjustment not re-applied
    assert r.monthly_pnl == 20


def test_third_trading_day_no_adjustment_in_daily_figures():
    r = _run(_eod(OCT2), "2026-10-03", adj={"X": 30})
    assert r.daily_pnl_prev_day == 5   # 155 - 150
    assert r.intraday_pnl == 5         # 160 - 155
    assert r.monthly_pnl == 20


def test_negative_adjustment():
    r = _run(_eod(SEP), "2026-10-01", adj={"X": -30})
    assert r.intraday_pnl == 80
    assert r.monthly_pnl == 80


def test_adjustment_only_hits_listed_account():
    eod = _eod(SEP + [("Y", "20260929", 200.0), ("Y", "20260930", 210.0)])
    live = pd.DataFrame({"client_no": ["X", "Y"], "equity_bal": [160.0, 260.0]})
    out = compute_eod_pnl(eod, live, pd.Timestamp("2026-10-01"), adjustments={"X": 30}).set_index("client_no")
    assert out.loc["X", "intraday_pnl"] == 20
    assert out.loc["Y", "intraday_pnl"] == 50


def test_single_eod_has_no_prev_day():
    r = _run(_eod([("X", "20260930", 110.0)]), "2026-10-01", adj={"X": 30})
    assert pd.isna(r.daily_pnl_prev_day)
    assert r.intraday_pnl == 20


def test_missing_live_equity_gives_none_pnl():
    live = pd.DataFrame({"client_no": ["Z"], "equity_bal": [1.0]})
    r = _run(_eod(SEP), "2026-10-01", adj={"X": 30}, live=live)
    assert pd.isna(r.intraday_pnl) and pd.isna(r.monthly_pnl)
