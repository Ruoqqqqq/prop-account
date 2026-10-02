import pandas as pd

from utils import archive_db


def test_credit_excess_roundtrip_and_overwrite(tmp_path):
    db = tmp_path / "t.db"
    archive_db.set_credit_excess(["A", "B"], 500.0, "initial", db_path=db)
    archive_db.set_credit_excess(["A"], 750.0, "revised", db_path=db)
    assert archive_db.load_credit_excess_map(db) == {"A": 750.0, "B": 500.0}
    archive_db.clear_credit_excess("B", db_path=db)
    assert archive_db.load_credit_excess_map(db) == {"A": 750.0}


def test_credit_excess_removes_false_intraday_pnl():
    eod = pd.DataFrame({"client_no": ["A"], "report_date": ["20261001"], "total_equity": [1000.0]})
    live = pd.DataFrame({"client_no": ["A"], "equity_bal": [1500.0]})  # 500 is static credit excess
    live["equity_bal"] = live["equity_bal"] - live["client_no"].map({"A": 500.0}).fillna(0)
    out = archive_db.compute_eod_pnl(eod, live, pd.Timestamp("2026-10-02")).iloc[0]
    assert out.intraday_pnl == 0
