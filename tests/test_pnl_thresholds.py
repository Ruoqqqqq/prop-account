from utils import pnl_thresholds as t


def test_no_default_fallback():
    cfg = {"default": {"intraday_pnl": -1.0}, "accounts": {"A": {"intraday_pnl": -5.0, "monthly_pnl": None}}}
    assert t.effective_threshold(cfg, "A", "intraday_pnl") == -5.0
    assert t.effective_threshold(cfg, "A", "monthly_pnl") is None   # no silent fallback
    assert t.effective_threshold(cfg, "B", "intraday_pnl") is None  # unlisted account never alerts


def test_legacy_default_key_is_dropped(tmp_path, monkeypatch):
    f = tmp_path / "th.json"
    f.write_text('{"default": {"intraday_pnl": -50000}, "accounts": {"A": {"intraday_pnl": -1}}}')
    monkeypatch.setattr(t, "THRESHOLDS_PATH", f)
    cfg = t.load_config()
    assert "default" not in cfg and cfg["accounts"]["A"]["intraday_pnl"] == -1
