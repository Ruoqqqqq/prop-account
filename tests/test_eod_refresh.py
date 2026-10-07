import pandas as pd
import pytest

from utils import archive_db, data_loader, eod_runner, jasper_downloader


def _fs(date, equity):
    return pd.DataFrame({
        "Report_Date": [date] * 2, "Client_No": ["A", "B"],
        "Equity_Collateral_Marginable_Securities": equity,
    })


def _eod_rows(db, rows):
    conn = archive_db.get_connection(db)
    conn.executemany(
        "INSERT INTO eod_snapshots (client_no, report_date, total_equity, archived_at) VALUES (?, ?, ?, 'x')", rows
    )
    conn.commit()
    conn.close()


# ---- adjustment applies on the month's first trading day only
def test_adjustment_uses_only_first_trading_day_of_month(tmp_path):
    db = tmp_path / "t.db"
    _eod_rows(db, [("A", "20261001", 1.0), ("A", "20261002", 1.0), ("A", "20260930", 1.0)])
    adj = pd.DataFrame({
        "report_date": ["20261001", "20261002", "20260930"], "client_no": ["A"] * 3, "amount": [-500.0, -500.0, 70.0],
    })
    archive_db.save_daily_adjustments(adj, ["A"], db_path=db)
    assert archive_db.load_adjustments_map("2026-10", db_path=db) == {"A": -500.0}   # not -1000
    assert archive_db.load_adjustments_map("2026-09", db_path=db) == {"A": 70.0}    # only 30 Sep archived -> that is Sept's first day
    assert archive_db.load_adjustments_map("2026-08", db_path=db) == {}             # no EOD for the month at all


def test_uploaded_adjustment_overrides_file(tmp_path):
    db = tmp_path / "t.db"
    _eod_rows(db, [("A", "20261001", 1.0)])
    archive_db.save_daily_adjustments(
        pd.DataFrame({"report_date": ["20261001"], "client_no": ["A"], "amount": [-500.0]}), ["A"], db_path=db)
    archive_db.add_adjustments(pd.DataFrame({"client_no": ["A"], "amount": [-123.0]}), "2026-10", None, db_path=db)
    assert archive_db.load_adjustments_map("2026-10", db_path=db) == {"A": -123.0}


def test_load_adjustment_file_finds_header_and_base_sgd_rows(tmp_path):
    path = tmp_path / "adj.xlsx"
    pd.DataFrame([
        ["Monthly adjustment", None, None, None],
        ["Report_Date", "Client_No", "Curr_Cd", "Adjustments"],
        ["20261001", "A", "BASE_SGD", "1,500.50"],
        ["20261001", "A", "USD", "9"],
    ]).to_excel(path, header=False, index=False)
    out = data_loader.load_adjustment_file(path)
    assert out.to_dict("records") == [{"report_date": "20261001", "client_no": "A", "amount": 1500.5}]


# ---- once-a-day EOD refresh
@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    state = {"fs": _fs("20261006", [100.0, 200.0])}
    monkeypatch.setattr(eod_runner.jasper_downloader, "load_config", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(eod_runner.data_loader, "load_financial_summary", lambda p: state["fs"])
    monkeypatch.setattr(eod_runner.data_loader, "financial_summary_path", lambda d: tmp_path / "fs.xls")
    (tmp_path / "fs.xls").write_text("x")
    monkeypatch.setattr(
        eod_runner.data_loader, "build_prop_snapshot",
        lambda d: (pd.DataFrame({"Client_No": ["A", "B"]}), pd.Timestamp("2026-10-07"), tmp_path / "eq.xls"),
    )
    return db, state, tmp_path


NOW = pd.Timestamp("2026-10-07 10:30")


def test_empty_financial_summary_is_not_ready_and_keeps_old_data(env):
    db, state, d = env
    _eod_rows(db, [("A", "20261005", 90.0)])
    state["fs"] = _fs("20261007", [100.0, 200.0]).iloc[0:0]
    r = eod_runner.run_eod_refresh(d, now=NOW, db_path=db)
    assert r["status"] == "not_ready"
    assert archive_db.get_state(eod_runner.STATE_KEY, db_path=db) is None   # retry later
    assert len(archive_db.load_eod_history(db_path=db)) == 1               # history untouched


def test_future_dated_or_all_zero_file_rejected(env):
    db, state, d = env
    state["fs"] = _fs("20261008", [100.0, 200.0])
    assert eod_runner.run_eod_refresh(d, now=NOW, db_path=db)["status"] == "not_ready"
    state["fs"] = _fs("20261006", [0.0, 0.0])
    assert eod_runner.run_eod_refresh(d, now=NOW, db_path=db)["status"] == "not_ready"


def test_good_file_archives_once_then_skips_rest_of_day(env):
    db, state, d = env
    r = eod_runner.run_eod_refresh(d, now=NOW, db_path=db)
    assert r["status"] == "ok" and r["eod_rows"] == 2
    again = eod_runner.run_eod_refresh(d, now=NOW + pd.Timedelta(minutes=20), db_path=db)
    assert again["status"] == "already_done"


def test_same_trade_date_again_is_no_new_date(env):
    db, state, d = env
    eod_runner.run_eod_refresh(d, now=NOW, db_path=db)
    r = eod_runner.run_eod_refresh(d, now=pd.Timestamp("2026-10-08 10:30"), db_path=db)  # next day, file unchanged
    assert r["status"] == "no_new_date"
    assert archive_db.get_state(eod_runner.STATE_KEY, db_path=db) == "2026-10-07"


def test_keyword_sets():
    cfg = {"keywords": {"live": "L", "eod": "E"}}
    assert jasper_downloader.keyword_for(cfg, "live") == "L"
    assert jasper_downloader.keyword_for(cfg, "eod") == "E"
    assert jasper_downloader.keyword_for({"keyword": "old"}, "live") == "old"
    with pytest.raises(ValueError):
        jasper_downloader.keyword_for({"keyword": "old"}, "eod")
