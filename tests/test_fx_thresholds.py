import pandas as pd

from utils import alert_engine, fx

CFG = {"accounts": {"A": {"intraday_pnl": -1000.0, "daily_pnl_prev_day": None, "monthly_pnl": None}}}


def _merged(intraday):
    return pd.DataFrame({
        "Client_No": ["A", "B"],
        "intraday_pnl": [intraday, -10**9],  # B has no threshold, so never alerts
        "daily_pnl_prev_day": [0.0, 0.0],
        "monthly_pnl": [0.0, 0.0],
        "breaching_past_testing": [False, False],
        "other_real_breach": [False, False],
    })


def test_usd_threshold_converted_to_sgd():
    # USD -1000 at 1.30 = SGD -1300
    assert alert_engine.threshold_sgd(CFG, "A", "intraday_pnl", 1.30) == -1300.0
    hits = alert_engine.compute_pnl_alert_hits(_merged(-1200.0), CFG, 1.30)["intraday_pnl"]
    assert not hits.iloc[0]   # SGD -1200 is above SGD -1300: no alert (it would wrongly fire un-converted)
    hits = alert_engine.compute_pnl_alert_hits(_merged(-1300.0), CFG, 1.30)["intraday_pnl"]
    assert hits.iloc[0]
    assert not hits.iloc[1]


def test_alert_message_shows_both_currencies():
    alerts = alert_engine.compute_alerts(_merged(-2000.0), CFG, 1.30)
    msg = [a["message"] for a in alerts if a["alert_type"] == "pnl_intraday_pnl"][0]
    assert "SGD -2,000.00" in msg and "USD -1,000.00" in msg and "1.3000" in msg


def test_no_rate_means_no_pnl_alerts(monkeypatch):
    monkeypatch.setattr(alert_engine.fx, "get_usd_sgd", lambda force=False: None)
    hits = alert_engine.compute_pnl_alert_hits(_merged(-10**9), CFG)
    assert not any(h.any() for h in hits.values())


def test_falls_back_to_saved_rate_when_feed_down(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(fx.archive_db, "DB_PATH", db)
    monkeypatch.setattr(fx, "_cache", {"at": 0.0, "value": None})
    fx._save(1.28, "2026-10-05", db_path=db)
    monkeypatch.setattr(fx, "_fetch", lambda: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(fx, "_load_saved", lambda db_path=db: fx.FxRate(1.28, "2026-10-05", "x", live=False))
    got = fx.get_usd_sgd(force=True)
    assert got.rate == 1.28 and got.live is False
