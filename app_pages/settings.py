import pandas as pd
import streamlit as st

from utils import admin_auth, archive_db, data_loader, pnl_thresholds
from utils.cached_loaders import load_prop_snapshot
from utils.data_loader import DATA_DIR

st.title("Settings")
st.caption(
    "Everything that changes how Prop accounts figures and alerts are calculated. "
    "Viewing is open; changing anything needs the admin password."
)

admin = admin_auth.render_unlock()

result = load_prop_snapshot()
if result is None:
    st.warning("No equity monitor file or `MarginNowV2.xls` found, so the account list isn't available.")
    st.stop()
live, _report_ts, _eq_path = result
client_nos = live["Client_No"].tolist()


def _saved(message: str) -> None:
    st.cache_data.clear()
    st.toast(message, icon=":material/check_circle:")


# ---------------------------------------------------------------- credit excess
with st.container(border=True):
    st.subheader("Credit excess (excluded from live balance)")
    st.caption(
        "MarginNowV2's live balance carries a static credit excess that FinancialSummary's EOD balance doesn't, "
        "which would otherwise show up as a false intraday/monthly P&L. The amount saved here is **subtracted** "
        "from the account's live balance before any P&L or alert is calculated. Enter the amount as it appears "
        "in the live balance: positive if it inflates the live balance, negative if it deflates it."
    )
    if admin:
        with st.form("credit_excess_form", border=False):
            ce_accounts = st.multiselect(
                "Accounts", sorted(client_nos), key="ce_accounts",
                help="Select several accounts to give them the same amount at once.",
            )
            with st.container(horizontal=True):
                ce_amount = st.number_input("Credit excess amount", value=0.0, step=1000.0, key="ce_amount")
                ce_note = st.text_input("Note (optional)", key="ce_note")
            if st.form_submit_button("Save credit excess", icon=":material/save:"):
                if not ce_accounts:
                    st.warning("Select at least one account first.")
                else:
                    archive_db.set_credit_excess(ce_accounts, ce_amount, ce_note or None)
                    _saved(f"Saved credit excess for {len(ce_accounts)} account(s)")

    ce_df = archive_db.load_credit_excess()
    if ce_df.empty:
        st.caption("No credit excess configured.")
    else:
        ce_view = ce_df.rename(columns={"client_no": "Account", "amount": "Amount", "note": "Note", "updated_at": "Updated at"})
        ce_config = {"Amount": st.column_config.NumberColumn(format="%.2f")}
        if not admin:
            st.dataframe(ce_view, hide_index=True, column_config=ce_config)
        else:
            st.caption("Edit an amount or note directly in the table, then click Save edits.")
            ce_edited = st.data_editor(
                ce_view, hide_index=True, column_config=ce_config, disabled=["Account", "Updated at"],
                key="ce_editor",
            )
            if st.button("Save edits", icon=":material/save:", key="ce_edit_save"):
                changed = 0
                for (_, before), (_, after) in zip(ce_view.iterrows(), ce_edited.iterrows()):
                    same_note = (before["Note"] if pd.notna(before["Note"]) else "") == (after["Note"] if pd.notna(after["Note"]) else "")
                    if before["Amount"] == after["Amount"] and same_note:
                        continue
                    if pd.isna(after["Amount"]):
                        st.warning(f"{after['Account']}: amount can't be blank (use Remove to delete it).")
                        continue
                    archive_db.set_credit_excess(
                        [after["Account"]], float(after["Amount"]), after["Note"] if pd.notna(after["Note"]) and after["Note"] else None
                    )
                    changed += 1
                if changed:
                    _saved(f"Updated credit excess for {changed} account(s)")
                    st.rerun()
                else:
                    st.info("No changes to save.")
            with st.container(horizontal=True):
                clear_acct = st.selectbox("Remove credit excess for", ce_df["client_no"], key="ce_clear_acct")
                if st.button("Remove", icon=":material/delete:", key="ce_clear_btn"):
                    archive_db.clear_credit_excess(clear_acct)
                    _saved(f"Removed credit excess for {clear_acct}")
                    st.rerun()

# ---------------------------------------------------------------- P&L thresholds
with st.container(border=True):
    st.subheader("P&L alert thresholds")
    st.caption(
        "Set a loss floor per metric for each account. An account alerts when its P&L falls at or below "
        "its threshold. There is no default: an account with no threshold here is never alerted on. "
        "Saving overwrites all three metrics for the selected accounts; a blank field means no alert for that metric."
    )
    if admin:
        def _load_account_thresholds() -> None:
            accts = st.session_state.get("pnl_th_accounts") or []
            if len(accts) != 1:
                # Several (or no) accounts selected -- their thresholds may differ, so leave the
                # fields blank rather than showing one account's values as if they applied to all.
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
            help="Select several accounts to give them the same thresholds at once. "
            "Selecting a single account loads its existing thresholds, if any.",
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
            if st.form_submit_button("Save thresholds", icon=":material/save:"):
                th_accounts = st.session_state.pnl_th_accounts
                if not th_accounts:
                    st.warning("Select at least one account first.")
                else:
                    for th_account in th_accounts:
                        pnl_thresholds.set_account_thresholds(th_account, {
                            "intraday_pnl": th_acct_intraday, "daily_pnl_prev_day": th_acct_daily,
                            "monthly_pnl": th_acct_monthly,
                        })
                    _saved(f"Saved thresholds for {len(th_accounts)} account(s)")

    thresholds = pnl_thresholds.load_config()["accounts"]
    if not thresholds:
        st.caption("No thresholds configured — no P&L alerts will fire.")
    else:
        th_table = pd.DataFrame(
            [{"Account": acct, **{pnl_thresholds.METRICS[m]: v for m, v in metrics.items()}}
             for acct, metrics in sorted(thresholds.items())]
        )
        th_config = {name: st.column_config.NumberColumn(format="%.2f") for name in pnl_thresholds.METRICS.values()}
        if not admin:
            st.dataframe(th_table, hide_index=True, column_config=th_config)
        else:
            st.caption("Edit a threshold directly in the table (clear a cell for no alert on that metric), then click Save edits.")
            th_edited = st.data_editor(
                th_table, hide_index=True, column_config=th_config, disabled=["Account"], key="th_editor",
            )
            if st.button("Save edits", icon=":material/save:", key="th_edit_save"):
                metric_by_label = {label: key for key, label in pnl_thresholds.METRICS.items()}
                changed = 0
                for (_, before), (_, after) in zip(th_table.iterrows(), th_edited.iterrows()):
                    if before.equals(after):
                        continue
                    pnl_thresholds.set_account_thresholds(after["Account"], {
                        metric_by_label[label]: (float(after[label]) if pd.notna(after[label]) else None)
                        for label in metric_by_label
                    })
                    changed += 1
                if changed:
                    _saved(f"Updated thresholds for {changed} account(s)")
                    st.rerun()
                else:
                    st.info("No changes to save.")
        if admin:
            with st.container(horizontal=True):
                clear_th_acct = st.selectbox("Remove thresholds for", sorted(thresholds), key="pnl_th_clear_acct")
                if st.button("Remove", icon=":material/delete:", key="pnl_th_clear_btn"):
                    pnl_thresholds.clear_account_thresholds(clear_th_acct)
                    _saved(f"Removed thresholds for {clear_th_acct}")
                    st.rerun()
    missing = sorted(set(client_nos) - set(thresholds))
    if missing:
        st.caption(f"Accounts with no thresholds (never alerted): {', '.join(missing)}")

# ---------------------------------------------------------------- monthly adjustments
with st.container(border=True):
    st.subheader("Monthly equity adjustments")
    st.caption(
        "Upload the start-of-month adjustment file (deposits/withdrawals/corrections) so P&L excludes them — "
        "these aren't trading P&L. Positive amount = equity added, negative = equity removed. The amount is "
        "removed from the month's first trading day (intraday, then previous-day P&L) and from monthly P&L."
    )
    if admin:
        adj_file = st.file_uploader("Adjustment file", type=["csv", "xls", "xlsx"], key="adj_upload")
        adj_raw = None
        if adj_file is not None:
            try:
                adj_raw = pd.read_csv(adj_file) if adj_file.name.lower().endswith(".csv") else pd.read_excel(adj_file)
            except Exception as exc:  # noqa: BLE001 -- surface any parse failure to the user
                st.error(f"Could not read {adj_file.name}: {exc}")

        if adj_raw is not None:
            # Not inside an st.form on purpose: a form only reruns on submit, so changing a
            # dropdown wouldn't update the preview until Save was clicked. Preview and saved
            # rows are built from separate Series so choosing the same column twice is harmless.
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
                    _saved(f"Saved {rows_saved} adjustment row(s) for {adj_month}")

    current_month = pd.Timestamp.now().strftime("%Y-%m")
    existing_adj = archive_db.load_adjustments(month=current_month)
    existing_adj = existing_adj[existing_adj["client_no"].isin(client_nos)]
    if existing_adj.empty:
        st.caption(f"No adjustments recorded yet for {current_month}.")
    else:
        st.caption(f"Adjustments recorded for {current_month}:")
        st.dataframe(
            existing_adj[["client_no", "amount", "note", "uploaded_at"]].rename(
                columns={"client_no": "Account", "amount": "Amount", "note": "Note", "uploaded_at": "Uploaded at"}
            ),
            hide_index=True,
        )

# ---------------------------------------------------------------- testing periods
with st.container(border=True):
    st.subheader("Testing periods")
    st.caption(
        "Set a testing period for an account when a breach is due to testing, not a real risk issue. "
        "If it's still breaching after the period ends, it's flagged on the Prop accounts page."
    )
    if admin:
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
                _saved(f"Testing period added for {testing_account}")

    testing_periods_df = archive_db.load_testing_periods()
    testing_periods_df = testing_periods_df[testing_periods_df["client_no"].isin(client_nos)]
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
                                "Download approval proof", full_path.read_bytes(), file_name=full_path.name,
                                icon=":material/download:", key=f"testing_proof_dl_{period_id}",
                            )
                else:
                    st.caption("No approval proof attached.")

                if not admin:
                    st.write(r["note"] or "No note.")
                    continue

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
                        _saved("Testing period updated")
                        st.rerun()

                if delete_clicked:
                    archive_db.delete_testing_period(period_id)
                    _saved("Testing period deleted")
                    st.rerun()

# ---------------------------------------------------------------- EOD backfill
with st.container(border=True):
    st.subheader("Backfill historical EOD data")
    st.caption(
        "Upload FinancialSummary.xls files for previous trade dates to test/verify daily and monthly P&L. "
        "Each file's own Report_Date is used (not today's date), and dates already archived are skipped "
        "automatically — safe to re-upload the same file. This doesn't touch the live data/FinancialSummary.xls."
    )
    if not admin:
        st.caption("Locked — unlock above to upload.")
    else:
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
