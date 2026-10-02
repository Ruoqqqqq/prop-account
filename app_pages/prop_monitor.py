import pandas as pd
import streamlit as st

from utils import alert_engine, archive_db, data_loader, pnl_thresholds
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

with st.container(border=True):
    st.subheader("Backfill historical EOD data")
    st.caption(
        "Upload FinancialSummary.xls files for previous trade dates to test/verify daily and monthly "
        "P&L. Each file's own Report_Date is used (not today's date), and dates already archived are "
        "skipped automatically — safe to re-upload the same file. This doesn't touch the live "
        "data/FinancialSummary.xls."
    )
    uploaded_files = st.file_uploader(
        "FinancialSummary.xls files", type=["xls"], accept_multiple_files=True, key="eod_backfill_upload",
    )
    if uploaded_files and st.button("Archive uploaded files", icon=":material/upload:"):
        for uploaded_file in uploaded_files:
            try:
                fs_df = data_loader.load_financial_summary(uploaded_file)
            except Exception as exc:  # noqa: BLE001 -- surface any parse failure to the user
                st.error(f"{uploaded_file.name}: could not read file ({exc})")
                continue
            report_dates = sorted(fs_df["Report_Date"].dropna().unique())
            new_rows = archive_db.archive_eod_if_new(fs_df, client_nos)
            dates_label = ", ".join(report_dates) if report_dates else "unknown"
            if new_rows:
                st.success(f"{uploaded_file.name}: archived {new_rows} new row(s) for date(s) {dates_label}")
            else:
                st.info(f"{uploaded_file.name}: date(s) {dates_label} already archived, no new rows")

with st.container(border=True):
    st.subheader("Monthly equity adjustments")
    st.caption(
        "Upload the start-of-month adjustment file (deposits/withdrawals/corrections) so monthly P&L "
        "excludes them — these aren't trading P&L. Positive amount = equity added, negative = equity "
        "removed; monthly P&L subtracts whatever you save here for the month it applies to."
    )
    adj_file = st.file_uploader("Adjustment file", type=["csv", "xls", "xlsx"], key="adj_upload")
    adj_raw = None
    if adj_file is not None:
        try:
            adj_raw = pd.read_csv(adj_file) if adj_file.name.lower().endswith(".csv") else pd.read_excel(adj_file)
        except Exception as exc:  # noqa: BLE001 -- surface any parse failure to the user
            st.error(f"Could not read {adj_file.name}: {exc}")

    if adj_raw is not None:
        # Selecting the same column for both dropdowns (e.g. both left at Streamlit's
        # default of the first column, before the user has touched either one) must not
        # select it twice into one DataFrame -- pandas/pyarrow reject duplicate column
        # names outright when rendering the preview. Build both the preview and the
        # saved rows from separate Series instead of a [[account_col, amount_col]]
        # double-selection, so a duplicate choice is harmless either way.
        #
        # These widgets are deliberately NOT inside an st.form: a form only reruns the
        # script (and thus recomputes the preview below) on submit, so changing either
        # dropdown wouldn't update the preview until "Save adjustments" was clicked --
        # it would silently keep showing whatever columns were selected by default.
        with st.container(horizontal=True):
            account_col = st.selectbox("Account number column", adj_raw.columns, key="adj_account_col")
            default_amount_idx = 1 if len(adj_raw.columns) > 1 else 0
            amount_col = st.selectbox(
                "Adjustment amount column", adj_raw.columns, index=default_amount_idx, key="adj_amount_col",
            )
        with st.container(horizontal=True):
            adj_month = st.text_input(
                "Applies to month (YYYY-MM)", value=pd.Timestamp.now().strftime("%Y-%m"), key="adj_month",
            )
            adj_note = st.text_input("Note (optional)", key="adj_note")
        st.caption("Preview:")
        preview_df = pd.DataFrame({"Account": adj_raw[account_col], "Amount": adj_raw[amount_col]}).head(20)
        st.dataframe(preview_df, hide_index=True)

        if st.button("Save adjustments", icon=":material/save:", key="adj_save_btn"):
            try:
                pd.Period(adj_month, freq="M")
            except Exception:
                st.warning("Month must be in YYYY-MM format.")
            else:
                to_save = pd.DataFrame({"client_no": adj_raw[account_col], "amount": adj_raw[amount_col]})
                to_save["client_no"] = to_save["client_no"].astype(str).str.strip()
                to_save["amount"] = pd.to_numeric(to_save["amount"], errors="coerce")
                to_save = to_save.dropna(subset=["amount"])
                rows_saved = archive_db.add_adjustments(to_save, adj_month, adj_note or None)
                st.toast(f"Saved {rows_saved} adjustment row(s) for {adj_month}", icon=":material/check_circle:")

    current_month = pd.Timestamp.now().strftime("%Y-%m")
    existing_adj = archive_db.load_adjustments(month=current_month)
    existing_adj = existing_adj[existing_adj["client_no"].isin(client_nos)]
    if existing_adj.empty:
        st.caption(f"No adjustments recorded yet for {current_month}.")
    else:
        st.caption(f"Adjustments recorded for {current_month} (excluded from monthly P&L below):")
        st.dataframe(
            existing_adj[["client_no", "amount", "note", "uploaded_at"]].rename(
                columns={"client_no": "Account", "amount": "Amount", "note": "Note", "uploaded_at": "Uploaded at"}
            ),
            hide_index=True,
        )

with st.container(border=True):
    st.subheader("P&L alert thresholds")
    st.caption(
        "Set a default loss floor per metric, then optionally override it for specific accounts. "
        "Any account whose P&L falls at or below its effective threshold triggers an alert below. "
        "Leave a field blank to disable alerting for that metric."
    )
    threshold_config = pnl_thresholds.load_config()

    st.markdown("**Default (applies to every account without its own override)**")
    with st.form("pnl_default_threshold_form", border=False):
        with st.container(horizontal=True):
            th_intraday = st.number_input(
                "Intraday P&L alert ≤", value=threshold_config["default"].get("intraday_pnl"), step=1000.0,
                key="pnl_th_default_intraday",
            )
            th_daily = st.number_input(
                "Daily P&L (prev day) alert ≤", value=threshold_config["default"].get("daily_pnl_prev_day"),
                step=1000.0, key="pnl_th_default_daily",
            )
            th_monthly = st.number_input(
                "Monthly P&L alert ≤", value=threshold_config["default"].get("monthly_pnl"), step=1000.0,
                key="pnl_th_default_monthly",
            )
        submitted_default_thresholds = st.form_submit_button("Save default", icon=":material/save:")

    if submitted_default_thresholds:
        threshold_config["default"] = {
            "intraday_pnl": th_intraday, "daily_pnl_prev_day": th_daily, "monthly_pnl": th_monthly,
        }
        pnl_thresholds.save_config(threshold_config)
        st.toast("Default P&L thresholds saved", icon=":material/check_circle:")

    st.markdown("**Per-account overrides**")

    def _load_account_thresholds() -> None:
        accts = st.session_state.get("pnl_th_accounts") or []
        if len(accts) != 1:
            # Multiple (or no) accounts selected -- their existing overrides may differ,
            # so leave the fields blank rather than showing one account's values as if
            # they applied to all of them.
            st.session_state.pnl_th_acct_intraday = None
            st.session_state.pnl_th_acct_daily = None
            st.session_state.pnl_th_acct_monthly = None
            return
        existing = pnl_thresholds.load_config()["accounts"].get(accts[0], {})
        st.session_state.pnl_th_acct_intraday = existing.get("intraday_pnl")
        st.session_state.pnl_th_acct_daily = existing.get("daily_pnl_prev_day")
        st.session_state.pnl_th_acct_monthly = existing.get("monthly_pnl")

    st.multiselect(
        "Accounts", sorted(client_nos), key="pnl_th_accounts", on_change=_load_account_thresholds,
        help="Select one or more accounts to set the same override for all of them at once. "
        "Selecting a single account loads its existing override, if any.",
    )
    with st.form("pnl_account_threshold_form", border=False):
        with st.container(horizontal=True):
            th_acct_intraday = st.number_input(
                "Intraday P&L alert ≤", value=None, step=1000.0, key="pnl_th_acct_intraday",
            )
            th_acct_daily = st.number_input(
                "Daily P&L (prev day) alert ≤", value=None, step=1000.0, key="pnl_th_acct_daily",
            )
            th_acct_monthly = st.number_input(
                "Monthly P&L alert ≤", value=None, step=1000.0, key="pnl_th_acct_monthly",
            )
        st.caption("Leave a field blank to fall back to the default for that metric.")
        submitted_account_thresholds = st.form_submit_button("Save account override", icon=":material/save:")

    if submitted_account_thresholds:
        th_accounts = st.session_state.pnl_th_accounts
        if not th_accounts:
            st.warning("Select at least one account first.")
        else:
            for th_account in th_accounts:
                pnl_thresholds.set_account_thresholds(th_account, {
                    "intraday_pnl": th_acct_intraday, "daily_pnl_prev_day": th_acct_daily,
                    "monthly_pnl": th_acct_monthly,
                })
            st.toast(f"Saved threshold override for {len(th_accounts)} account(s)", icon=":material/check_circle:")

    threshold_config = pnl_thresholds.load_config()  # reload so a just-saved change applies immediately below
    current_overrides = threshold_config["accounts"]
    if current_overrides:
        st.caption("Current overrides:")
        for acct, metrics in sorted(current_overrides.items()):
            with st.container(horizontal=True):
                vals = ", ".join(
                    f"{pnl_thresholds.METRICS[m]}: {v:,.2f}" for m, v in metrics.items() if v is not None
                ) or "no metrics set"
                st.write(f"**{acct}** — {vals}")
                if st.button("Clear", key=f"clear_pnl_override_{acct}", icon=":material/close:"):
                    pnl_thresholds.clear_account_thresholds(acct)
                    st.toast(f"Cleared override for {acct}", icon=":material/check_circle:")
                    st.rerun()
    else:
        st.caption("No per-account overrides set — every account uses the default.")

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


pnl_alert_hits = alert_engine.compute_pnl_alert_hits(merged, threshold_config)
any_pnl_alert = pd.Series(False, index=merged.index)
for hit in pnl_alert_hits.values():
    any_pnl_alert |= hit

# Log to the shared alert history (shown in the panel on every tab), and keep it in sync
# even when nobody has this page open -- the scheduled snapshot job calls this too.
current_alerts = alert_engine.compute_alerts(merged, threshold_config)
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
            th = pnl_thresholds.effective_threshold(threshold_config, row["Client_No"], metric_col)
            parts.append(f"{row['Client_No']} ({row[metric_col]:,.2f} ≤ {th:,.2f})")
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
            "Last EOD equity": st.column_config.NumberColumn(format="%.2f"),
            "Intraday P&L": st.column_config.NumberColumn(format="%.2f"),
            "Daily P&L (prev day)": st.column_config.NumberColumn(format="%.2f"),
            "Monthly P&L": st.column_config.NumberColumn(format="%.2f"),
            "Position ratio": st.column_config.NumberColumn(format="%.2f"),
        },
    )

with st.container(border=True):
    st.subheader("Testing periods")
    st.caption(
        "Set a testing period for an account when a breach is due to testing, not a real risk issue. "
        "If it's still breaching after the period ends, it's flagged above and in the Breach column."
    )

    with st.form("testing_period_form", border=False):
        with st.container(horizontal=True):
            testing_account = st.selectbox("Account", sorted(client_nos), key="testing_account")
            testing_start = st.date_input("Start date", key="testing_start")
            testing_end = st.date_input("End date", key="testing_end")
        testing_note = st.text_input("Note (optional)", key="testing_note", placeholder="e.g. reason for testing")
        testing_proof = st.file_uploader(
            "Approval email (optional)", type=["png", "jpg", "jpeg", "pdf", "msg", "eml"], key="testing_proof",
            help="Upload the approval email (exported as .msg/.eml/.pdf) or a screenshot as proof.",
        )
        submitted_testing = st.form_submit_button("Add testing period", icon=":material/schedule:")

    if submitted_testing:
        if testing_end < testing_start:
            st.warning("End date must be on or after the start date.")
        else:
            archive_db.add_testing_period(
                testing_account, testing_start.isoformat(), testing_end.isoformat(), testing_note or None,
                proof_bytes=testing_proof.getvalue() if testing_proof is not None else None,
                proof_filename=testing_proof.name if testing_proof is not None else None,
            )
            st.toast(f"Testing period added for {testing_account}", icon=":material/check_circle:")

    if testing_periods_df.empty:
        st.caption("No testing periods logged yet.")
    else:
        for _, r in testing_periods_df.iterrows():
            period_id = int(r["id"])
            label = f"{r['client_no']} — {r['start_date']:%Y-%m-%d} to {r['end_date']:%Y-%m-%d}"
            with st.expander(label):
                proof_path = r.get("proof_path")
                has_proof = pd.notna(proof_path)
                if has_proof:
                    full_path = DATA_DIR.parent / proof_path
                    if full_path.exists():
                        if full_path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                            st.image(str(full_path))
                        else:
                            st.download_button(
                                "Download approval proof",
                                full_path.read_bytes(),
                                file_name=full_path.name,
                                icon=":material/download:",
                                key=f"testing_proof_dl_{period_id}",
                            )
                else:
                    st.caption("No approval proof attached.")

                with st.form(f"edit_testing_{period_id}", border=False):
                    with st.container(horizontal=True):
                        edit_start = st.date_input(
                            "Start date", value=r["start_date"].date(), key=f"edit_start_{period_id}"
                        )
                        edit_end = st.date_input(
                            "End date", value=r["end_date"].date(), key=f"edit_end_{period_id}"
                        )
                    edit_note = st.text_input("Note", value=r["note"] or "", key=f"edit_note_{period_id}")
                    edit_proof = st.file_uploader(
                        "Replace approval proof (optional)", type=["png", "jpg", "jpeg", "pdf", "msg", "eml"],
                        key=f"edit_proof_{period_id}",
                    )
                    remove_proof = (
                        st.checkbox("Remove existing proof", key=f"edit_remove_proof_{period_id}")
                        if has_proof else False
                    )
                    with st.container(horizontal=True):
                        save_clicked = st.form_submit_button("Save changes", icon=":material/save:")
                        delete_clicked = st.form_submit_button("Delete", icon=":material/delete:")

                if save_clicked:
                    if edit_end < edit_start:
                        st.warning("End date must be on or after the start date.")
                    else:
                        archive_db.update_testing_period(
                            period_id, edit_start.isoformat(), edit_end.isoformat(), edit_note or None,
                            proof_bytes=edit_proof.getvalue() if edit_proof is not None else None,
                            proof_filename=edit_proof.name if edit_proof is not None else None,
                            remove_proof=remove_proof,
                        )
                        st.toast("Testing period updated", icon=":material/check_circle:")
                        st.rerun()

                if delete_clicked:
                    archive_db.delete_testing_period(period_id)
                    st.toast("Testing period deleted", icon=":material/check_circle:")
                    st.rerun()

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
