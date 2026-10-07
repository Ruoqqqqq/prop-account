import pandas as pd
import streamlit as st

from utils import alert_engine, archive_db, fx, identity, pnl_thresholds
from utils.cached_loaders import load_prop_snapshot
from utils.data_loader import DATA_DIR
from utils.snapshot_runner import run_snapshot

st.title("Overview")
st.caption("Real-time status of every prop account. P&L and equity figures are in SGD.")

result = load_prop_snapshot()
if result is None:
    st.warning(
        "No equity monitor file found (expected a file matching "
        "`Propriety_Account_Equity_Monitor_V2*.xls`) or `MarginNowV2.xls` is missing."
    )
    st.stop()

live, report_ts, eq_path = result
client_nos = live["Client_No"].tolist()
threshold_config = pnl_thresholds.load_config()

prop_status = alert_engine.build_prop_status(DATA_DIR)
merged = prop_status.merged
eod_history = prop_status.eod_history
real_breach = merged["real_breach"]
breaching_past_testing = merged["breaching_past_testing"]

fx_rate = fx.get_usd_sgd()
usd_sgd = fx_rate.rate if fx_rate else None
pnl_alert_hits = alert_engine.compute_pnl_alert_hits(merged, threshold_config, usd_sgd)
any_pnl_alert = pd.Series(False, index=merged.index)
for hit in pnl_alert_hits.values():
    any_pnl_alert |= hit

# Keep the shared alert log current on every load (the scheduled job does the same).
archive_db.sync_alerts("prop_accounts", alert_engine.compute_alerts(merged, threshold_config, usd_sgd))

alerts = archive_db.load_alerts_with_status()
today = pd.Timestamp.now().normalize()
open_alerts = int((alerts["resolved_at"].isna() & alerts["handled_by"].isna()).sum())
breach_alerts_today = alerts[alerts["alert_type"].str.startswith("breach") & (alerts["triggered_at"] >= today)]


def _total_or_dash(series: pd.Series) -> str:
    return f"{series.sum():,.2f}" if series.notna().any() else "—"


# ------------------------------------------------------------------ focus numbers
with st.container(border=True):
    with st.container(horizontal=True):
        st.metric("Prop accounts", f"{len(merged):,}", border=True)
        st.metric("Breaching now", f"{int(real_breach.sum())}", border=True,
                  help="Accounts flagged as breaching and not covered by an active testing period.")
        st.metric("Breach alerts today", f"{breach_alerts_today['client_no'].nunique()}", border=True,
                  help="Distinct accounts that triggered a breach alert today, including ones that have since cleared.")
        st.metric("Past testing period", f"{int(breaching_past_testing.sum())}", border=True)
        st.metric("Past P&L threshold", f"{int(any_pnl_alert.sum())}", border=True)
        st.metric("Open alerts", f"{open_alerts}", border=True, help="Alerts nobody has acted on yet. See the Alert audit tab.")
    with st.container(horizontal=True):
        st.metric("P&L today (intraday)", _total_or_dash(merged["intraday_pnl"]), border=True)
        st.metric("P&L previous day", _total_or_dash(merged["daily_pnl_prev_day"]), border=True)
        st.metric("P&L month to date", _total_or_dash(merged["monthly_pnl"]), border=True)
        st.metric("Total live equity", _total_or_dash(merged["equity_ex_credit"]), border=True,
                  help="Live adjusted equity excluding credit excess.")

with st.container(horizontal=True, vertical_alignment="center"):
    st.caption(f"Source: {eq_path.name}")
    st.caption(f"Report time: {report_ts}")
    if fx_rate is not None:
        st.caption(f"USD/SGD {fx_rate.rate:.4f} ({fx_rate.rate_date})")
    if st.button("Run snapshot now", icon=":material/save:", help="Download the latest data from Jasper and archive the current state immediately"):
        outcome = run_snapshot(DATA_DIR)
        st.cache_data.clear()
        for error in outcome["errors"]:
            st.warning(error)
        if outcome["eq_source"] is not None:
            snapshot_note = (
                f"archived {outcome['prop_rows']} prop rows ({outcome['alert_count']} alert(s))"
                if outcome["prop_rows"] else "no active alerts, snapshot not archived"
            )
            st.toast(f"{snapshot_note}, {outcome['eod_rows']} new EOD rows", icon=":material/check_circle:")

if fx_rate is None:
    st.error("No USD/SGD exchange rate available (feed unreachable and none saved yet), so P&L threshold alerts can't be evaluated.")
elif not fx_rate.live:
    st.warning(f"USD/SGD feed unreachable — using last saved rate {fx_rate.rate:.4f} (rate date {fx_rate.rate_date}).")
if eod_history.empty:
    st.info(
        "No EOD financial summary archived yet — intraday/daily/monthly P&L will appear once "
        "`FinancialSummary.xls` has been captured."
    )
elif merged["daily_pnl_prev_day"].isna().all():
    st.info("Only one EOD date archived so far — previous-day P&L will appear once a second date is captured.")

# ------------------------------------------------------------------ all real-time data
with st.container(border=True):
    st.subheader("All prop accounts")
    labels = {
        "Client_No": "Client no.", "AE Code": "AE code", "Client Grp": "Client group", "Account_Type": "Account type",
        "Position_Ratio": "Position ratio", "Net_Positions_Alert": "Net positions alert", "intra_lots": "Intra lots",
        "Breach": "Breach", "Testing status": "Testing status",
        "Equity_Bal": "Equity bal", "Adj. Equity Bal with Coll.": "Current equity (adj. w/ coll.)",
        "credit_excess": "Credit excess", "equity_ex_credit": "Current equity (excl. credit excess)",
        "last_eod_date": "Last EOD date", "last_eod_equity": "Last EOD equity",
        "intraday_pnl": "Intraday P&L", "daily_pnl_prev_day": "Daily P&L (prev day)", "monthly_pnl": "Monthly P&L",
        "NLV": "NLV", "IM": "IM", "MM": "MM", "Margin Ratio (%)": "Margin ratio (%)",
        "Ledger_Bal": "Ledger bal", "UPLVal": "Unrealised P&L",
    }
    internal = {
        "client_no", "is_breach", "real_breach", "is_active_testing", "is_lapsed_testing",
        "breaching_past_testing", "other_real_breach",
    }
    ordered = [c for c in labels if c in merged.columns]
    extras = [c for c in merged.columns if c not in labels and c not in internal]
    table = merged[ordered + extras].rename(columns=labels)
    money_like = [c for c in table.columns if table[c].dtype.kind == "f"]
    st.dataframe(
        table, hide_index=True,
        column_config={c: st.column_config.NumberColumn(format="%.2f") for c in money_like},
    )

with st.container(border=True):
    st.subheader("Equity trend")
    history = archive_db.load_history()
    history = history[history["client_no"].isin(client_nos)]
    if history.empty:
        st.info("No archived history yet — this builds up over time as snapshots run.")
    else:
        selected_account = st.selectbox("Account", sorted(history["client_no"].unique()))
        acct_history = history[history["client_no"] == selected_account].sort_values("captured_at")
        st.line_chart(acct_history, x="captured_at", y="equity_bal")

with st.container(border=True):
    st.subheader("Remarks & supporting documents")
    st.caption("Log a note and, if there's a breach, attach a screenshot as supporting documentation. Recorded under your name.")

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
                username=identity.current_user(),
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
            by = f" · {r['username']}" if pd.notna(r.get("username")) and r["username"] else ""
            with st.expander(f"{r['client_no']} — {r['created_at']:%Y-%m-%d %H:%M}{by}"):
                if r["remark_text"]:
                    st.write(r["remark_text"])
                if r["screenshot_path"]:
                    img_path = DATA_DIR.parent / r["screenshot_path"]
                    if img_path.exists():
                        st.image(str(img_path))
