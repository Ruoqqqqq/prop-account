"""SQLite archive of hourly prop-account snapshots. No Streamlit imports."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "prop_archive.db"

ATTACHMENTS_DIR = DATA_DIR / "attachments"

SCHEMA = """
CREATE TABLE IF NOT EXISTS prop_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    report_ts TEXT,
    client_no TEXT NOT NULL,
    ae_code TEXT,
    client_grp TEXT,
    position_ratio REAL,
    intra_lots REAL,
    breach TEXT,
    equity_bal REAL,
    nlv REAL,
    im REAL,
    mm REAL,
    margin_ratio REAL
);
CREATE INDEX IF NOT EXISTS idx_prop_snapshots_client_time
    ON prop_snapshots (client_no, captured_at);

CREATE TABLE IF NOT EXISTS eod_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_no TEXT NOT NULL,
    report_date TEXT NOT NULL,
    total_equity REAL,
    archived_at TEXT NOT NULL,
    UNIQUE(client_no, report_date)
);
CREATE INDEX IF NOT EXISTS idx_eod_snapshots_client_date
    ON eod_snapshots (client_no, report_date);

CREATE TABLE IF NOT EXISTS remarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_no TEXT NOT NULL,
    created_at TEXT NOT NULL,
    remark_text TEXT,
    screenshot_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_remarks_client_time
    ON remarks (client_no, created_at);

CREATE TABLE IF NOT EXISTS equity_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_no TEXT NOT NULL,
    month TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    uploaded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_adjustments_client_month
    ON equity_adjustments (client_no, month);

CREATE TABLE IF NOT EXISTS credit_excess (
    client_no TEXT PRIMARY KEY,
    amount REAL NOT NULL,
    note TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    username TEXT NOT NULL,
    remark TEXT,
    file_path TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_actions_alert ON alert_actions (alert_id);

CREATE TABLE IF NOT EXISTS settings_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    username TEXT NOT NULL,
    area TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS daily_adjustments (
    report_date TEXT NOT NULL,
    client_no TEXT NOT NULL,
    amount REAL NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (report_date, client_no)
);

CREATE TABLE IF NOT EXISTS job_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS fx_rates (
    pair TEXT PRIMARY KEY,
    rate REAL NOT NULL,
    rate_date TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS testing_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_no TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    note TEXT,
    proof_path TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_testing_periods_client
    ON testing_periods (client_no);

CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_at TEXT NOT NULL,
    resolved_at TEXT,
    source TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    client_no TEXT,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_log_active
    ON alert_log (source, alert_type, client_no, resolved_at);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Add a column to an already-existing table if it predates this schema version."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _ensure_column(conn, "testing_periods", "proof_path", "TEXT")
    _ensure_column(conn, "remarks", "username", "TEXT")
    return conn


def insert_snapshot(df: pd.DataFrame, captured_at: pd.Timestamp, report_ts: pd.Timestamp | None,
                     db_path: Path = DB_PATH) -> int:
    """Append one row per account for this capture. Returns rows inserted."""
    rename = {
        "Client_No": "client_no",
        "AE Code": "ae_code",
        "Client Grp": "client_grp",
        "Position_Ratio": "position_ratio",
        "intra_lots": "intra_lots",
        "Breach": "breach",
        "Adj. Equity Bal with Coll.": "equity_bal",
        "NLV": "nlv",
        "IM": "im",
        "MM": "mm",
        "Margin Ratio (%)": "margin_ratio",
    }
    cols_present = {src: dst for src, dst in rename.items() if src in df.columns}
    out = df[list(cols_present.keys())].rename(columns=cols_present).copy()
    out["captured_at"] = captured_at.isoformat()
    out["report_ts"] = report_ts.isoformat() if report_ts is not None else None

    conn = get_connection(db_path)
    try:
        out.to_sql("prop_snapshots", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()
    return len(out)


def load_history(client_no: str | None = None, start: pd.Timestamp | None = None,
                  end: pd.Timestamp | None = None, db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM prop_snapshots WHERE 1=1"
        params: list = []
        if client_no:
            query += " AND client_no = ?"
            params.append(client_no)
        if start is not None:
            query += " AND captured_at >= ?"
            params.append(start.isoformat())
        if end is not None:
            query += " AND captured_at <= ?"
            params.append(end.isoformat())
        query += " ORDER BY captured_at ASC"
        df = pd.read_sql_query(query, conn, params=params, parse_dates=["captured_at", "report_ts"])
    finally:
        conn.close()
    return df


def latest_capture_time(db_path: Path = DB_PATH) -> pd.Timestamp | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT MAX(captured_at) FROM prop_snapshots").fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None:
        return None
    return pd.Timestamp(row[0])


def snapshot_count(db_path: Path = DB_PATH) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM prop_snapshots").fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def archive_eod_if_new(financial_summary_df: pd.DataFrame, client_nos: list[str],
                        db_path: Path = DB_PATH) -> int:
    """Archive today's EOD Equity_Collateral_Marginable_Securities per account, skipping dates already stored.

    FinancialSummary.xls is refreshed in place by Jasper and only ever holds
    one date at a time, so this is safe to call on every scheduled run --
    the UNIQUE(client_no, report_date) constraint makes repeat calls for a
    date already archived a no-op. Returns the number of new rows inserted.
    """
    subset = financial_summary_df[financial_summary_df["Client_No"].isin(client_nos)]
    if subset.empty:
        return 0

    archived_at = pd.Timestamp.now().isoformat()
    conn = get_connection(db_path)
    try:
        inserted = 0
        for _, row in subset.iterrows():
            cur = conn.execute(
                "INSERT OR IGNORE INTO eod_snapshots (client_no, report_date, total_equity, archived_at) "
                "VALUES (?, ?, ?, ?)",
                (row["Client_No"], row["Report_Date"], row["Equity_Collateral_Marginable_Securities"], archived_at),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


def load_eod_history(client_no: str | None = None, db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM eod_snapshots WHERE 1=1"
        params: list = []
        if client_no:
            query += " AND client_no = ?"
            params.append(client_no)
        query += " ORDER BY report_date ASC"
        df = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()
    return df


def compute_eod_pnl(eod_history: pd.DataFrame, live_equity: pd.DataFrame, now: pd.Timestamp,
                     adjustments: dict[str, float] | None = None) -> pd.DataFrame:
    """Intraday / previous-day / monthly P&L anchored to archived EOD closes.

    live_equity: DataFrame with columns client_no, equity_bal (today's live
    figure -- MarginNowV2's "Adj. Equity Bal with Coll."). eod_history's
    total_equity is sourced from FinancialSummary's
    Equity_Collateral_Marginable_Securities (see archive_eod_if_new).
    adjustments: optional {client_no: amount} of this-month capital
    adjustments (deposits/withdrawals/corrections) to exclude from monthly
    P&L, since those aren't trading P&L (see load_adjustments_map).
    - intraday_pnl: live equity now minus the most recent archived EOD close.
    - daily_pnl_prev_day: the last archived EOD close minus the one before it
      (a fixed prior-day figure, not moving intraday). NaN until 2 EOD dates
      have been archived.
    - monthly_pnl: live equity now minus the EOD close from before this month
      started (falls back to the earliest archived EOD if none precedes it),
      minus this month's equity adjustment for that account (if any).
    The adjustment is booked on the month's first trading day, so it is also
    removed from the daily figure that spans that day: intraday_pnl while the
    last EOD is still the prior month's close, and daily_pnl_prev_day once the
    last EOD is the month's first trading day. Matches the Excel monitor's
    Update_MTD_History (adjustment applied to first-trade-date P&L only).
    """
    columns = ["client_no", "last_eod_date", "last_eod_equity", "intraday_pnl", "daily_pnl_prev_day", "monthly_pnl"]
    if eod_history.empty:
        return pd.DataFrame(columns=columns)

    adjustments = adjustments or {}
    month_start = now.normalize().replace(day=1)
    live_map = dict(zip(live_equity["client_no"], live_equity["equity_bal"]))

    rows = []
    for client_no, g in eod_history.groupby("client_no"):
        g = g.sort_values("report_date")
        last_eod_date = g["report_date"].iloc[-1]
        last_eod_equity = g["total_equity"].iloc[-1]
        prev_eod_equity = g["total_equity"].iloc[-2] if len(g) >= 2 else None

        g_dates = pd.to_datetime(g["report_date"], format="%Y%m%d")
        before_month = g[g_dates < month_start]
        monthly_baseline = before_month["total_equity"].iloc[-1] if not before_month.empty else g["total_equity"].iloc[0]

        adj = adjustments.get(client_no, 0)
        in_month = g[g_dates >= month_start]
        first_trade_date = in_month["report_date"].iloc[0] if not in_month.empty else None
        intraday_adj = adj if g_dates.iloc[-1] < month_start else 0
        prev_day_adj = adj if first_trade_date is not None and last_eod_date == first_trade_date else 0

        live_now = live_map.get(client_no)
        monthly_pnl = None
        if live_now is not None:
            monthly_pnl = (live_now - monthly_baseline) - adj

        rows.append({
            "client_no": client_no,
            "last_eod_date": last_eod_date,
            "last_eod_equity": last_eod_equity,
            "intraday_pnl": (live_now - last_eod_equity - intraday_adj) if live_now is not None else None,
            "daily_pnl_prev_day": (last_eod_equity - prev_eod_equity - prev_day_adj) if prev_eod_equity is not None else None,
            "monthly_pnl": monthly_pnl,
        })
    return pd.DataFrame(rows, columns=columns)


def add_remark(client_no: str, remark_text: str, screenshot_bytes: bytes | None,
                screenshot_filename: str | None, db_path: Path = DB_PATH,
                username: str | None = None) -> None:
    screenshot_path = None
    if screenshot_bytes:
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = pd.Timestamp.now().strftime("%Y%m%d%H%M%S%f")
        dest = ATTACHMENTS_DIR / f"{client_no}_{stamp}_{screenshot_filename}"
        dest.write_bytes(screenshot_bytes)
        screenshot_path = str(dest.relative_to(BASE_DIR))

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO remarks (client_no, created_at, remark_text, screenshot_path, username) VALUES (?, ?, ?, ?, ?)",
            (client_no, pd.Timestamp.now().isoformat(), remark_text, screenshot_path, username),
        )
        conn.commit()
    finally:
        conn.close()


def load_remarks(client_no: str | None = None, db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM remarks WHERE 1=1"
        params: list = []
        if client_no:
            query += " AND client_no = ?"
            params.append(client_no)
        query += " ORDER BY created_at DESC"
        df = pd.read_sql_query(query, conn, params=params, parse_dates=["created_at"])
    finally:
        conn.close()
    return df


def add_adjustments(adjustments_df: pd.DataFrame, month: str, note: str | None,
                     db_path: Path = DB_PATH) -> int:
    """Bulk-insert equity adjustments for a given month.

    adjustments_df: DataFrame with columns client_no, amount (already
    mapped/renamed by the caller from whatever columns the uploaded file
    used). month: "YYYY-MM". Multiple uploads for the same account/month
    are all kept and summed at query time (load_adjustments_map), so
    correcting an adjustment means uploading a follow-up row rather than
    needing to delete the old one.
    """
    out = adjustments_df[["client_no", "amount"]].copy()
    out["month"] = month
    out["note"] = note
    out["uploaded_at"] = pd.Timestamp.now().isoformat()

    conn = get_connection(db_path)
    try:
        out.to_sql("equity_adjustments", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()
    return len(out)


def load_adjustments(client_no: str | None = None, month: str | None = None,
                      db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM equity_adjustments WHERE 1=1"
        params: list = []
        if client_no:
            query += " AND client_no = ?"
            params.append(client_no)
        if month:
            query += " AND month = ?"
            params.append(month)
        query += " ORDER BY month DESC, uploaded_at DESC"
        df = pd.read_sql_query(query, conn, params=params, parse_dates=["uploaded_at"])
    finally:
        conn.close()
    return df


def set_credit_excess(client_nos: list[str], amount: float, note: str | None,
                       db_path: Path = DB_PATH) -> None:
    """Set (or replace) the static credit excess for each account.

    MarginNowV2's live balance carries a static credit excess that FinancialSummary's
    EOD balance doesn't, so it has to come off the live balance before comparing the two.
    """
    now = pd.Timestamp.now().isoformat()
    conn = get_connection(db_path)
    try:
        conn.executemany(
            "INSERT INTO credit_excess (client_no, amount, note, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(client_no) DO UPDATE SET amount=excluded.amount, note=excluded.note, "
            "updated_at=excluded.updated_at",
            [(c, float(amount), note, now) for c in client_nos],
        )
        conn.commit()
    finally:
        conn.close()


def clear_credit_excess(client_no: str, db_path: Path = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM credit_excess WHERE client_no = ?", (client_no,))
        conn.commit()
    finally:
        conn.close()


def load_credit_excess(db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    try:
        return pd.read_sql_query(
            "SELECT * FROM credit_excess ORDER BY client_no", conn, parse_dates=["updated_at"]
        )
    finally:
        conn.close()


def load_credit_excess_map(db_path: Path = DB_PATH) -> dict[str, float]:
    df = load_credit_excess(db_path)
    return dict(zip(df["client_no"], df["amount"]))


def load_adjustments_map(month: str, db_path: Path = DB_PATH) -> dict[str, float]:
    """{client_no: total adjustment amount} for the given month.

    Uploaded adjustments (Settings page) win if there are any for the month. Otherwise the
    adjustment file downloaded from Jasper is used -- but only the rows for the month's
    *first trading day*; the file exists every day and later days must not be applied.
    """
    df = load_adjustments(month=month, db_path=db_path)
    if not df.empty:
        return df.groupby("client_no")["amount"].sum().to_dict()

    conn = get_connection(db_path)
    try:
        prefix = month.replace("-", "")
        row = conn.execute(
            "SELECT MIN(report_date) FROM eod_snapshots WHERE report_date LIKE ?", (prefix + "%",)
        ).fetchone()
        first_date = row[0] if row else None
        if first_date is None:
            return {}
        rows = conn.execute(
            "SELECT client_no, SUM(amount) FROM daily_adjustments WHERE report_date = ? GROUP BY client_no",
            (first_date,),
        ).fetchall()
    finally:
        conn.close()
    return {client_no: amount for client_no, amount in rows}


def save_daily_adjustments(adjustments_df: pd.DataFrame, client_nos: list[str],
                            db_path: Path = DB_PATH) -> int:
    """Store the adjustment file's rows per report date (replacing any earlier copy of that date/account)."""
    subset = adjustments_df[adjustments_df["client_no"].isin(client_nos)]
    if subset.empty:
        return 0
    now = pd.Timestamp.now().isoformat()
    conn = get_connection(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_adjustments (report_date, client_no, amount, ingested_at) VALUES (?, ?, ?, ?)",
            [(r.report_date, r.client_no, float(r.amount), now) for r in subset.itertuples()],
        )
        conn.commit()
    finally:
        conn.close()
    return len(subset)


def get_state(key: str, db_path: Path = DB_PATH) -> str | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT value FROM job_state WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def set_state(key: str, value: str, db_path: Path = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO job_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def add_testing_period(client_no: str, start_date: str, end_date: str, note: str | None,
                        proof_bytes: bytes | None = None, proof_filename: str | None = None,
                        db_path: Path = DB_PATH) -> None:
    """start_date/end_date: 'YYYY-MM-DD'. proof_bytes: e.g. a saved approval email (.msg/.eml/.pdf) or screenshot."""
    proof_path = None
    if proof_bytes:
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = pd.Timestamp.now().strftime("%Y%m%d%H%M%S%f")
        dest = ATTACHMENTS_DIR / f"{client_no}_testingproof_{stamp}_{proof_filename}"
        dest.write_bytes(proof_bytes)
        proof_path = str(dest.relative_to(BASE_DIR))

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO testing_periods (client_no, start_date, end_date, note, proof_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (client_no, start_date, end_date, note, proof_path, pd.Timestamp.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def load_testing_periods(client_no: str | None = None, db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM testing_periods WHERE 1=1"
        params: list = []
        if client_no:
            query += " AND client_no = ?"
            params.append(client_no)
        query += " ORDER BY start_date DESC"
        df = pd.read_sql_query(
            query, conn, params=params, parse_dates=["start_date", "end_date", "created_at"],
        )
    finally:
        conn.close()
    return df


def update_testing_period(period_id: int, start_date: str, end_date: str, note: str | None,
                           proof_bytes: bytes | None = None, proof_filename: str | None = None,
                           remove_proof: bool = False, db_path: Path = DB_PATH) -> None:
    """Edit an existing testing period. Replacing or removing the proof deletes the old file."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT client_no, proof_path FROM testing_periods WHERE id = ?", (period_id,)
        ).fetchone()
        if row is None:
            return
        client_no, old_proof_path = row

        new_proof_path = old_proof_path
        if proof_bytes:
            ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = pd.Timestamp.now().strftime("%Y%m%d%H%M%S%f")
            dest = ATTACHMENTS_DIR / f"{client_no}_testingproof_{stamp}_{proof_filename}"
            dest.write_bytes(proof_bytes)
            new_proof_path = str(dest.relative_to(BASE_DIR))
        elif remove_proof:
            new_proof_path = None

        conn.execute(
            "UPDATE testing_periods SET start_date = ?, end_date = ?, note = ?, proof_path = ? WHERE id = ?",
            (start_date, end_date, note, new_proof_path, period_id),
        )
        conn.commit()
    finally:
        conn.close()

    if old_proof_path and old_proof_path != new_proof_path:
        old_file = BASE_DIR / old_proof_path
        if old_file.exists():
            old_file.unlink()


def delete_testing_period(period_id: int, db_path: Path = DB_PATH) -> None:
    """Delete a testing period and its associated proof file, if any."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT proof_path FROM testing_periods WHERE id = ?", (period_id,)).fetchone()
        proof_path = row[0] if row else None
        conn.execute("DELETE FROM testing_periods WHERE id = ?", (period_id,))
        conn.commit()
    finally:
        conn.close()

    if proof_path:
        proof_file = BASE_DIR / proof_path
        if proof_file.exists():
            proof_file.unlink()


def testing_status(client_no: str, testing_periods: pd.DataFrame, today: pd.Timestamp) -> tuple[str, pd.Timestamp] | None:
    """Returns (status, end_date) for the given account, or None if no period is on file.

    status is "active" if today falls within some period's [start, end], else
    "lapsed" (using the most recently ended period) if only past periods exist.
    """
    periods = testing_periods[testing_periods["client_no"] == client_no]
    if periods.empty:
        return None
    today = today.normalize()
    active = periods[(periods["start_date"] <= today) & (periods["end_date"] >= today)]
    if not active.empty:
        return "active", active["end_date"].max()
    lapsed = periods[periods["end_date"] < today]
    if not lapsed.empty:
        return "lapsed", lapsed["end_date"].max()
    return None


def sync_alerts(source: str, current_alerts: list[dict], db_path: Path = DB_PATH) -> None:
    """Reconcile the alert log for `source` with the currently-true alert conditions.

    current_alerts: [{"alert_type": ..., "client_no": ..., "message": ...}, ...] --
    every alert condition that's true right now. Alerts not already logged as
    active get a new row; alerts logged as active that aren't in this list
    anymore get resolved_at set. Calling this repeatedly with the same active
    set is a no-op (safe to call on every page load / scheduled run).
    """
    now = pd.Timestamp.now().isoformat()
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, alert_type, client_no FROM alert_log WHERE source = ? AND resolved_at IS NULL",
            (source,),
        ).fetchall()
        active_db = {(alert_type, client_no): row_id for row_id, alert_type, client_no in rows}
        current_keys = {(a["alert_type"], a.get("client_no")) for a in current_alerts}

        for key, row_id in active_db.items():
            if key not in current_keys:
                conn.execute("UPDATE alert_log SET resolved_at = ? WHERE id = ?", (now, row_id))

        for alert in current_alerts:
            key = (alert["alert_type"], alert.get("client_no"))
            if key not in active_db:
                conn.execute(
                    "INSERT INTO alert_log (triggered_at, resolved_at, source, alert_type, client_no, message) "
                    "VALUES (?, NULL, ?, ?, ?, ?)",
                    (now, source, alert["alert_type"], alert.get("client_no"), alert["message"]),
                )
        conn.commit()
    finally:
        conn.close()


def load_alert_log(active_only: bool = False, db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM alert_log"
        if active_only:
            query += " WHERE resolved_at IS NULL"
        query += " ORDER BY triggered_at DESC"
        df = pd.read_sql_query(query, conn, parse_dates=["triggered_at", "resolved_at"])
    finally:
        conn.close()
    return df


def active_alert_count(db_path: Path = DB_PATH) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM alert_log WHERE resolved_at IS NULL").fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0



# --------------------------------------------------------------------------- alert handling
ALERT_ACTIONS = {
    "dismiss": "Dismissed with remark",
    "supporting": "Supporting document uploaded",
    "testing": "Testing progress logged",
}


def load_alerts_with_status(db_path: Path = DB_PATH) -> pd.DataFrame:
    """Every alert with who handled it (the first action taken) and how.

    An alert is *handled* once anyone takes any action on it; until then, while its condition
    is still true (resolved_at empty), it is *open* and needs action.
    """
    conn = get_connection(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT a.*, act.action AS action, act.username AS handled_by, act.remark AS action_remark,
                   act.file_path AS action_file, act.created_at AS handled_at
            FROM alert_log a
            LEFT JOIN alert_actions act
              ON act.id = (SELECT MIN(id) FROM alert_actions WHERE alert_id = a.id)
            ORDER BY a.triggered_at DESC
            """,
            conn, parse_dates=["triggered_at", "resolved_at", "handled_at"],
        )
    finally:
        conn.close()
    return df


def load_pending_alerts(db_path: Path = DB_PATH) -> pd.DataFrame:
    """Alerts whose condition is still true and that nobody has acted on yet."""
    df = load_alerts_with_status(db_path)
    return df[df["resolved_at"].isna() & df["handled_by"].isna()].sort_values("triggered_at").reset_index(drop=True)


def record_alert_action(alert_id: int, action: str, username: str, remark: str | None = None,
                         file_bytes: bytes | None = None, file_name: str | None = None,
                         db_path: Path = DB_PATH) -> tuple[bool, str | None]:
    """Claim an alert by taking an action on it. Returns (ok, handled_by).

    Only the first action wins: if a teammate already acted (even a moment ago, from another
    browser), nothing is written and (False, their_username) comes back so the UI can say
    the alert is already handled. (False, None) means the alert doesn't exist.
    """
    if action not in ALERT_ACTIONS:
        raise ValueError(f"unknown alert action {action!r}")
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")  # serialises two people acting at the same moment
        if conn.execute("SELECT 1 FROM alert_log WHERE id = ?", (alert_id,)).fetchone() is None:
            conn.rollback()
            return False, None
        existing = conn.execute(
            "SELECT username FROM alert_actions WHERE alert_id = ? ORDER BY id LIMIT 1", (alert_id,)
        ).fetchone()
        if existing:
            conn.rollback()
            return False, existing[0]

        file_path = None
        if file_bytes:
            dest_dir = ATTACHMENTS_DIR / "alerts"
            dest_dir.mkdir(parents=True, exist_ok=True)
            safe_name = Path(file_name or "attachment").name
            dest = dest_dir / f"alert{alert_id}_{pd.Timestamp.now():%Y%m%d%H%M%S%f}_{safe_name}"
            dest.write_bytes(file_bytes)
            file_path = str(dest.relative_to(BASE_DIR))
        conn.execute(
            "INSERT INTO alert_actions (alert_id, action, username, remark, file_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (alert_id, action, username, remark, file_path, pd.Timestamp.now().isoformat()),
        )
        conn.commit()
        return True, None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- settings audit trail
def log_settings_change(username: str, area: str, action: str, detail: str | None = None,
                         db_path: Path = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO settings_audit (created_at, username, area, action, detail) VALUES (?, ?, ?, ?, ?)",
            (pd.Timestamp.now().isoformat(), username, area, action, detail),
        )
        conn.commit()
    finally:
        conn.close()


def load_settings_audit(db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    try:
        return pd.read_sql_query(
            "SELECT * FROM settings_audit ORDER BY created_at DESC, id DESC", conn, parse_dates=["created_at"]
        )
    finally:
        conn.close()
