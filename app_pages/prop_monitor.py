import pandas as pd
import streamlit as st

from utils import alert_engine, archive_db, fx, pnl_thresholds
from utils.cached_loaders import load_prop_snapshot
from utils.snapshot_runner import run_snapshot
from utils.data_loader import DATA_DIR

st.title("Prop accounts")
st.caption("Intraday, previous-day, and monthly P&L, plus position ratio, for the prop account list.")

result = load_prop_snapshot()
if result is None:
    st.warning(
        "No equity monitor file found (expected a file matching "
        "`Propriety_Account_Equity_Monitor_V2-*.xls`) or `MarginNowV2.xls` is missing."
    )
    st.stop()

live, report_ts, eq_path = result

with st.container(horizontal=True):
    st.caption(f"Prop account list source: {eq_path.name}")
    st.caption(f"Report time: {report_ts}")
    _fx = fx.get_usd_sgd()
    if _fx is not None:
        st.caption(f"USD/SGD {_fx.rate:.4f} ({_fx.rate_date})")
    if st.button("Run snapshot now", icon=":material/save:", help="Fetch from Outlook and archive the current state immediately"):
        outcome = run_snapshot(DATA_DIR)
        st.cache_data.clear()
        for error in outcome["errors"]:
            st.warning(error)
        if outcome["eq_source"] is not None:
            snapshot_note = (
                f"archived {outcome['prop_rows']} prop rows ({outcome['alert_count']} alert(s))"
                if outcome["prop_rows"] else "no active alerts, snapshot not archived"
            )
            st.toast(
                f"{snapshot_note}, {outcome['eod_rows']} new EOD rows",
                icon=":material/check_circle:",
            )

client_nos = live["Client_No"].tolist()
threshold_config = pnl_thresholds.load_config()

history = archive_db.load_history()
history = history[history["client_no"].isin(client_nos)]

prop_status = alert_engine.build_prop_status(DATA_DIR)
merged = prop_status.merged
eod_history = prop_status.eod_history
testing_periods_df = prop_status.testing_periods_df
is_breach = merged["is_breach"]
real_breach = merged["real_breach"]
breaching_past_testing = merged["breaching_past_testing"]
other_real_breach = merged["other_real_breach"]

if eod_history.empty:
    st.info(
        "No EOD financial summary archived yet — intraday/daily/monthly P&L will appear once "
        "`FinancialSummary.xls` has been captured (happens automatically on each snapshot run)."
    )
elif merged["daily_pnl_prev_day"].isna().all():
    st.info("Only one EOD date archived so far — previous-day P&L will appear once a second date is captured.")

def _total_or_dash(series: pd.Series) -> str:
    return f"{series.sum():,.2f}" if series.notna().any() else "—"


fx_rate = fx.get_usd_sgd()
usd_sgd = fx_rate.rate if fx_rate else None
if fx_rate is None:
    st.error("No USD/SGD exchange rate available (feed unreachable and none saved yet), so P&L threshold alerts can't be evaluated.")
elif not fx_rate.live:
    st.warning(f"USD/SGD feed unreachable — using last saved rate {fx_rate.rate:.4f} (rate date {fx_rate.rate_date}).")
pnl_alert_hits = alert_engine.compute_pnl_alert_hits(merged, threshold_config, usd_sgd)
any_pnl_alert = pd.Series(False, index=merged.index)
for hit in pnl_alert_hits.values():
    any_pnl_alert |= hit

# Log to the shared alert history (shown in the panel on every tab), and keep it in sync
# even when nobody has this page open -- the scheduled snapshot job calls this too.
current_alerts = alert_engine.compute_alerts(merged, threshold_config, usd_sgd)
archive_db.sync_alerts("prop_accounts", current_alerts)

with st.container(horizontal=True):
    st.metric("Prop accounts", f"{len(merged):,}", border=True)
    st.metric("Breaching accounts", f"{int(real_breach.sum())}", border=True)
    st.metric("Breaching past testing period", f"{int(breaching_past_testing.sum())}", border=True)
    st.metric("Accounts past P&L threshold", f"{int(any_pnl_alert.sum())}", border=True)
    st.metric("Total intraday P&L", _total_or_dash(merged["intraday_pnl"]), border=True)
    st.metric("Total daily P&L (prev day)", _total_or_dash(merged["daily_pnl_prev_day"]), border=True)
    st.metric("Total monthly P&L", _total_or_dash(merged["monthly_pnl"]), border=True)

if breaching_past_testing.any():
    flagged = ", ".join(merged.loc[breaching_past_testing, "Client_No"])
    st.error(f"⚠ Still breaching after testing period ended: {flagged} — needs review.")
if other_real_breach.any():
    flagged = ", ".join(merged.loc[other_real_breach, "Client_No"])
    st.error(f"⚠ Account(s) currently flagged as breaching: {flagged}.")
if is_breach.any() and not real_breach.any():
    st.info("There's a breach, but it's fully covered by an active testing period — not flagged.")

for metric_col, hit in pnl_alert_hits.items():
    if hit.any():
        parts = []
        for _, row in merged.loc[hit].iterrows():
            th_sgd = alert_engine.threshold_sgd(threshold_config, row["Client_No"], metric_col, usd_sgd)
            th_usd = pnl_thresholds.effective_threshold(threshold_config, row["Client_No"], metric_col)
            parts.append(f"{row['Client_No']} (SGD {row[metric_col]:,.2f} ≤ SGD {th_sgd:,.2f} = USD {th_usd:,.2f})")
        st.error(f"⚠ {pnl_thresholds.METRICS[metric_col]} threshold breached: " + "; ".join(parts))

with st.container(border=True):
    st.subheader("Account status")
    display_cols = {
        "Client_No": "Client no.",
        "AE Code": "AE code",
        "Client Grp": "Client group",
        "Position_Ratio": "Position ratio",
        "intra_lots": "Intra lots",
        "Breach": "Breach",
        "Testing status": "Testing status",
        "Adj. Equity Bal with Coll.": "Current equity (adj. w/ coll.)",
        "credit_excess": "Credit excess",
        "equity_ex_credit": "Current equity (excl. credit excess)",
        "last_eod_date": "Last EOD date",
        "last_eod_equity": "Last EOD equity",
        "intraday_pnl": "Intraday P&L",
        "daily_pnl_prev_day": "Daily P&L (prev day)",
        "monthly_pnl": "Monthly P&L",
    }
    cols_present = [c for c in display_cols if c in merged.columns]
    table = merged[cols_present].rename(columns=display_cols)
    st.dataframe(
        table,
        hide_index=True,
        column_config={
            "Current equity (adj. w/ coll.)": st.column_config.NumberColumn(format="%.2f"),
            "Credit excess": st.column_config.NumberColumn(format="%.2f"),
            "Current equity (excl. credit excess)": st.column_config.NumberColumn(format="%.2f"),
            "Last EOD equity": st.column_config.NumberColumn(format="%.2f"),
            "Intraday P&L": st.column_config.NumberColumn(format="%.2f"),
            "Daily P&L (prev day)": st.column_config.NumberColumn(format="%.2f"),
            "Monthly P&L": st.column_config.NumberColumn(format="%.2f"),
            "Position ratio": st.column_config.NumberColumn(format="%.2f"),
        },
    )

with st.container(border=True):
    st.subheader("Equity trend")
    if history.empty:
        st.info("No archived history yet — this builds up over time as snapshots run.")
    else:
        selected_account = st.selectbox("Account", sorted(history["client_no"].unique()))
        acct_history = history[history["client_no"] == selected_account].sort_values("captured_at")
        st.line_chart(acct_history, x="captured_at", y="equity_bal")

with st.container(border=True):
    st.subheader("Remarks & supporting documents")
    st.caption("Log a note and, if there's a breach, attach a screenshot as supporting documentation.")

    with st.form("remark_form", border=False):
        remark_account = st.selectbox("Account", sorted(client_nos), key="remark_account")
        remark_text = st.text_area("Remark", placeholder="e.g. reason for breach / follow-up action")
        screenshot = st.file_uploader("Screenshot (optional)", type=["png", "jpg", "jpeg"])
        submitted = st.form_submit_button("Add remark", icon=":material/add_comment:")

    if submitted:
        if remark_text.strip() or screenshot is not None:
            archive_db.add_remark(
                remark_account,
                remark_text.strip(),
                screenshot.getvalue() if screenshot is not None else None,
                screenshot.name if screenshot is not None else None,
            )
            st.toast("Remark added", icon=":material/check_circle:")
        else:
            st.warning("Enter a remark or attach a screenshot before submitting.")

    remarks_df = archive_db.load_remarks()
    remarks_df = remarks_df[remarks_df["client_no"].isin(client_nos)]
    if remarks_df.empty:
        st.caption("No remarks logged yet.")
    else:
        for _, r in remarks_df.iterrows():
            label = f"{r['client_no']} — {r['created_at']:%Y-%m-%d %H:%M}"
            with st.expander(label):
                if r["remark_text"]:
                    st.write(r["remark_text"])
                if r["screenshot_path"]:
                    img_path = DATA_DIR.parent / r["screenshot_path"]
                    if img_path.exists():
                        st.image(str(img_path))
