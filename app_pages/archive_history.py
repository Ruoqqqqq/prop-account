import datetime as dt

import pandas as pd
import streamlit as st

from utils import archive_db

st.title("Archive history")
st.caption("Browse the hourly-archived prop account snapshots written by archive_snapshot.py.")

last_capture = archive_db.latest_capture_time()
total_rows = archive_db.snapshot_count()

with st.container(horizontal=True):
    st.metric("Snapshots archived", f"{total_rows:,}", border=True)
    st.metric("Last capture", last_capture.strftime("%Y-%m-%d %H:%M") if last_capture is not None else "—", border=True)

if total_rows == 0:
    st.info(
        "No archive data yet. Run `python archive_snapshot.py` once to seed it, "
        "and see the README for scheduling it hourly via Windows Task Scheduler."
    )
    st.stop()

all_history = archive_db.load_history()
accounts = sorted(all_history["client_no"].unique())
eod_history_full = archive_db.load_eod_history()


_adjustments_cache: dict[str, dict[str, float]] = {}


def _adjustments_for_month(month: str) -> dict[str, float]:
    if month not in _adjustments_cache:
        _adjustments_cache[month] = archive_db.load_adjustments_map(month)
    return _adjustments_cache[month]


def _credit_excess() -> dict[str, float]:
    if "_credit" not in _adjustments_cache:
        _adjustments_cache["_credit"] = archive_db.load_credit_excess_map()
    return _adjustments_cache["_credit"]


def _batch_pnl(batch_rows: pd.DataFrame) -> pd.DataFrame:
    """Per-account intraday/daily-prev-day/monthly P&L as of this batch's own capture time.

    Uses that batch's own equity_bal as the "live" figure and only the EOD
    dates that had already happened by then, so a batch reflects what was
    knowable at the time it was captured, not data backfilled since. Monthly
    P&L excludes that batch's own month's equity adjustments, same as the
    live Prop accounts page.
    """
    captured_at = batch_rows["captured_at"].iloc[0]
    cutoff = captured_at.strftime("%Y%m%d")
    eod_as_of_then = eod_history_full[eod_history_full["report_date"] <= cutoff]
    live_equity = batch_rows[["client_no", "equity_bal"]].copy()
    # Credit excess is static, so today's value is applied to every archived batch.
    live_equity["equity_bal"] = live_equity["equity_bal"] - live_equity["client_no"].map(_credit_excess()).fillna(0)
    adjustments = _adjustments_for_month(captured_at.strftime("%Y-%m"))
    pnl = archive_db.compute_eod_pnl(eod_as_of_then, live_equity, captured_at, adjustments=adjustments)
    cols = ["client_no", "intraday_pnl", "daily_pnl_prev_day", "monthly_pnl"]
    pnl = pnl[cols] if not pnl.empty else pd.DataFrame(columns=cols)
    return batch_rows.merge(pnl, on="client_no", how="left")

with st.form("archive_filters_form", border=True):
    with st.container(horizontal=True):
        selected_accounts = st.multiselect("Accounts", accounts, default=accounts)
        min_date = all_history["captured_at"].min().date()
        max_date = all_history["captured_at"].max().date()
        date_range = st.date_input(
            "Date range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
    st.form_submit_button("Apply", icon=":material/filter_alt:")

if isinstance(date_range, tuple) and len(date_range) == 2:
    range_start, range_end = date_range
else:
    range_start = range_end = date_range if not isinstance(date_range, tuple) else date_range[0]
start = dt.datetime.combine(range_start, dt.time.min)
end = dt.datetime.combine(range_end, dt.time.max)
filtered = all_history[
    all_history["client_no"].isin(selected_accounts)
    & (all_history["captured_at"] >= start)
    & (all_history["captured_at"] <= end)
].sort_values("captured_at", ascending=False)

with st.container(border=True):
    st.subheader("Position ratio over time")
    if selected_accounts:
        pivot = filtered.pivot_table(index="captured_at", columns="client_no", values="position_ratio")
        st.line_chart(pivot)
    else:
        st.info("Select at least one account to chart.")

with st.container(border=True):
    st.subheader("Archived snapshots")
    st.caption("One row per prop account monitor file update — download that batch's accounts below.")

    # A plain equality re-filter on (captured_at, report_ts) breaks when report_ts is
    # NaT: NaT == NaT is always False, so the "matching" batch_rows comes back empty
    # and batch_with_pnl's .iloc[0] raises IndexError. Tag each row with a stable
    # integer group id instead, which groupby assigns correctly even for NaN/NaT keys.
    filtered = filtered.copy()
    filtered["_batch_id"] = filtered.groupby(["captured_at", "report_ts"], dropna=False).ngroup()

    batch_summary = (
        filtered.groupby("_batch_id")
        .agg(
            captured_at=("captured_at", "first"),
            report_ts=("report_ts", "first"),
            accounts=("client_no", "nunique"),
            breaches=("breach", lambda s: (s.astype(str).str.upper() == "Y").sum()),
        )
        .reset_index()
        .sort_values("captured_at", ascending=False)
    )

    if batch_summary.empty:
        st.info("No archived batches match the current filters.")
    else:
        widths = [2, 2, 1, 1, 1.4]
        header_cols = st.columns(widths)
        for col, label in zip(header_cols, ["Captured at", "Report time", "Accounts", "Breaches", ""]):
            col.markdown(f"**{label}**")

        download_cols = {
            "client_no": "Account number",
            "position_ratio": "Position ratio",
            "intra_lots": "Intra lots",
            "breach": "Breach",
            "intraday_pnl": "Intraday P&L",
            "daily_pnl_prev_day": "Daily P&L (prev day)",
            "monthly_pnl": "Monthly P&L",
        }

        for _, row in batch_summary.iterrows():
            batch_rows = filtered[filtered["_batch_id"] == row["_batch_id"]]
            batch_with_pnl = _batch_pnl(batch_rows)
            download_df = batch_with_pnl[list(download_cols)].rename(columns=download_cols)

            cols = st.columns(widths)
            cols[0].write(row["captured_at"].strftime("%Y-%m-%d %H:%M:%S"))
            cols[1].write(row["report_ts"].strftime("%Y-%m-%d %H:%M:%S") if pd.notna(row["report_ts"]) else "—")
            cols[2].write(int(row["accounts"]))
            cols[3].write(int(row["breaches"]))
            cols[4].download_button(
                "Download",
                download_df.to_csv(index=False).encode("utf-8"),
                file_name=f"prop_batch_{row['captured_at']:%Y%m%d_%H%M%S}.csv",
                mime="text/csv",
                icon=":material/download:",
                key=f"dl_{row['captured_at'].isoformat()}_{row['report_ts']}",
            )
