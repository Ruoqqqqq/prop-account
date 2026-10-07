"""Alert handling UI shared by every page: the pop-up, the banner, and the action form.

An alert is *open* while its condition is true and nobody has acted on it. Anyone can
close it out by taking one of three actions -- dismiss with a remark, upload a supporting
document, or log testing progress -- all of which are recorded with the acting username
(archive_db.record_alert_action). Only the first action counts: if a teammate got there
first, the user is told "this alert is handled by one of your teammates".

streamlit_app.py calls mount() once per full script run:
  * a modal pops up for each open alert this browser session hasn't been shown yet,
  * a small banner on every page links to the Alert audit tab while alerts are open,
  * a 15-second fragment re-checks the shared alert log, so a new alert pops up (and an
    alert a teammate handled disappears, with a notice) without anyone clicking anything.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from utils import archive_db, identity

_SEEN_KEY = "alert_popup_seen"
_WATCH_KEY = "alert_watch_ids"
_FLASH_KEY = "alert_flash"
_DIALOG_KEY = "alert_dialog_id"
TEAMMATE_MESSAGE = "This alert is handled by one of your teammates."

_TYPE_LABELS = {
    "breach": "Breach",
    "breach_past_testing": "Breach past testing period",
    "pnl_intraday_pnl": "Intraday P&L threshold",
    "pnl_daily_pnl_prev_day": "Daily P&L (prev day) threshold",
    "pnl_monthly_pnl": "Monthly P&L threshold",
}


def type_label(alert_type: str) -> str:
    return _TYPE_LABELS.get(alert_type, alert_type)


@st.cache_data(ttl="30s", show_spinner=False)
def sync_alerts_cached() -> int:
    """Re-evaluate prop-account alerts into the shared log (shared by all sessions, 30s TTL)."""
    from utils import alert_engine
    from utils.data_loader import DATA_DIR

    return len(alert_engine.sync_prop_alerts(DATA_DIR))


def _safe_sync() -> None:
    try:
        sync_alerts_cached()
    except Exception:  # noqa: BLE001 -- an alert refresh failure must never break the page
        pass


def _seen() -> set:
    return st.session_state.setdefault(_SEEN_KEY, set())


def _flash(message: str) -> None:
    """Show a toast on the next full run (a st.rerun() straight after st.toast can drop it)."""
    st.session_state[_FLASH_KEY] = message


# ------------------------------------------------------------------------------ action form
def _submit(alert: dict, action: str, remark: str | None, upload=None, testing: tuple | None = None) -> None:
    """Record the action, then close the dialog / refresh. Handles the 'teammate got there first' case."""
    alert_id = int(alert["id"])
    user = identity.current_user()
    ok, handled_by = archive_db.record_alert_action(
        alert_id, action, user, remark,
        file_bytes=upload.getvalue() if upload is not None else None,
        file_name=upload.name if upload is not None else None,
    )
    _seen().add(alert_id)
    if ok:
        if action == "testing" and testing and str(alert["alert_type"]).startswith("breach"):
            archive_db.add_testing_period(
                alert["client_no"], testing[0].isoformat(), testing[1].isoformat(), remark,
                proof_bytes=upload.getvalue() if upload is not None else None,
                proof_filename=upload.name if upload is not None else None,
            )
        _flash(f"Alert for {alert['client_no']} handled — logged under {user}.")
    elif handled_by:
        _flash(TEAMMATE_MESSAGE)
    else:
        _flash("That alert no longer exists.")
    st.rerun()


def action_form(alert: dict, key: str) -> None:
    """The three ways to close out an open alert. Each submit rerenders the whole app."""
    tab_dismiss, tab_support, tab_testing = st.tabs(["Dismiss with remark", "Upload supporting document", "Testing progress"])

    with tab_dismiss:
        with st.form(f"{key}_dismiss", border=False):
            remark = st.text_area("Remark (required)", placeholder="Why is this alert OK / what was done about it?")
            if st.form_submit_button("Dismiss alert", icon=":material/check:"):
                if remark.strip():
                    _submit(alert, "dismiss", remark.strip())
                else:
                    st.warning("A remark is required to dismiss an alert.")

    with tab_support:
        with st.form(f"{key}_support", border=False):
            upload = st.file_uploader(
                "Supporting document", type=["png", "jpg", "jpeg", "pdf", "msg", "eml", "xlsx", "xls", "csv", "txt"],
                key=f"{key}_support_file",
            )
            note = st.text_input("Note (optional)")
            if st.form_submit_button("Submit supporting document", icon=":material/upload:"):
                if upload is None:
                    st.warning("Attach a file first.")
                else:
                    _submit(alert, "supporting", note.strip() or None, upload=upload)

    with tab_testing:
        st.caption(
            "Use when the alert is due to testing. For breach alerts this also sets a testing period "
            "for the account, so the breach is excused until it ends."
        )
        with st.form(f"{key}_testing", border=False):
            with st.container(horizontal=True):
                start = st.date_input("Testing start", key=f"{key}_t_start")
                end = st.date_input("Testing end", key=f"{key}_t_end")
            note = st.text_input("Testing note (required)", placeholder="What is being tested / current progress")
            proof = st.file_uploader(
                "Approval / progress evidence (optional)", type=["png", "jpg", "jpeg", "pdf", "msg", "eml"],
                key=f"{key}_t_file",
            )
            if st.form_submit_button("Log testing progress", icon=":material/science:"):
                if not note.strip():
                    st.warning("Add a note describing the testing.")
                elif end < start:
                    st.warning("End date must be on or after the start date.")
                else:
                    _submit(alert, "testing", note.strip(), upload=proof, testing=(start, end))


# ------------------------------------------------------------------------------ pop-up
def _on_dismiss() -> None:
    alert_id = st.session_state.get(_DIALOG_KEY)
    if alert_id is not None:
        _seen().add(alert_id)


@st.dialog("Alert needs action", width="large", on_dismiss=_on_dismiss)
def _alert_dialog(alert: dict, waiting: int) -> None:
    st.session_state[_DIALOG_KEY] = int(alert["id"])
    still_open = {int(i) for i in archive_db.load_pending_alerts()["id"]}
    if int(alert["id"]) not in still_open:
        st.info(f"{TEAMMATE_MESSAGE} (Or the condition has cleared.)")
        if st.button("Close", key="alert_dialog_close"):
            _seen().add(int(alert["id"]))
            st.rerun()
        return

    st.error(f"**{alert['client_no']}** — {type_label(alert['alert_type'])}")
    st.write(alert["message"])
    st.caption(f"Raised {pd.Timestamp(alert['triggered_at']):%Y-%m-%d %H:%M}. It stays open until someone acts on it.")
    if waiting:
        st.caption(f"{waiting} more open alert(s) after this one.")
    action_form(alert, key=f"popup_{int(alert['id'])}")
    if st.button("Decide later", type="tertiary", key="alert_dialog_later"):
        _seen().add(int(alert["id"]))
        st.rerun()


# ------------------------------------------------------------------------------ watcher + mount
@st.fragment(run_every="15s")
def _watch() -> None:
    """Poll the shared alert log; if the set of open alerts changed, do a full rerun."""
    _safe_sync()
    status = archive_db.load_alerts_with_status()
    now_open = {int(i) for i in status.loc[status["resolved_at"].isna() & status["handled_by"].isna(), "id"]}
    known = st.session_state.get(_WATCH_KEY, set())
    if now_open == known:
        return
    gone = known - now_open
    taken = status[status["id"].isin(gone) & status["handled_by"].notna() & (status["handled_by"] != identity.current_user())]
    if not taken.empty:
        _flash(TEAMMATE_MESSAGE + " (" + ", ".join(taken["client_no"].astype(str)) + ")")
    st.rerun()


def mount() -> None:
    flash = st.session_state.pop(_FLASH_KEY, None)
    if flash:
        st.toast(flash, icon=":material/info:")

    _safe_sync()
    pending = archive_db.load_pending_alerts()
    pending_ids = {int(i) for i in pending["id"]}
    st.session_state[_WATCH_KEY] = pending_ids
    seen = _seen()
    seen.intersection_update(pending_ids)

    if not pending.empty:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.warning(f"{len(pending)} open alert(s) need action.", icon=":material/notification_important:")
            st.page_link("app_pages/alert_audit.py", label="Open Alert audit", icon=":material/arrow_forward:")

    unseen = pending[~pending["id"].isin(seen)]
    if not unseen.empty:
        _alert_dialog(unseen.iloc[0].to_dict(), waiting=len(unseen) - 1)

    _watch()
