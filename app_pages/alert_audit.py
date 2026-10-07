import pandas as pd
import streamlit as st

from utils import alert_ui, archive_db
from utils.data_loader import DATA_DIR

st.title("Alert audit")
st.caption(
    "Every alert, who handled it and how. An alert stays open until someone dismisses it with a remark, "
    "uploads supporting documents, or logs testing progress."
)

alert_ui.sync_alerts_cached()
alerts = archive_db.load_alerts_with_status()


def _status(row) -> str:
    if pd.notna(row["handled_by"]):
        return "Handled — cleared" if pd.notna(row["resolved_at"]) else "Handled — still active"
    return "Cleared without action" if pd.notna(row["resolved_at"]) else "Open"


if alerts.empty:
    st.info("No alerts logged yet.")
    st.stop()

alerts["status"] = alerts.apply(_status, axis=1)
alerts["type_label"] = alerts["alert_type"].map(alert_ui.type_label)
alerts["action_label"] = alerts["action"].map(archive_db.ALERT_ACTIONS)
today = pd.Timestamp.now().normalize()

with st.container(horizontal=True):
    st.metric("Open (need action)", int((alerts["status"] == "Open").sum()), border=True)
    st.metric("Handled", int(alerts["handled_by"].notna().sum()), border=True)
    st.metric("Cleared without action", int((alerts["status"] == "Cleared without action").sum()), border=True)
    st.metric("Raised today", int((alerts["triggered_at"] >= today).sum()), border=True)

open_alerts = alerts[alerts["status"] == "Open"]
with st.container(border=True):
    st.subheader("Needs action")
    if open_alerts.empty:
        st.success("No open alerts.")
    for _, row in open_alerts.sort_values("triggered_at").iterrows():
        with st.expander(f"{row['client_no']} — {row['type_label']} (since {row['triggered_at']:%Y-%m-%d %H:%M})", expanded=False):
            st.write(row["message"])
            alert_ui.action_form(row.to_dict(), key=f"audit_{int(row['id'])}")

with st.container(border=True):
    st.subheader("Alert log")
    with st.form("alert_audit_filters", border=False):
        with st.container(horizontal=True):
            f_status = st.multiselect("Status", sorted(alerts["status"].unique()), default=sorted(alerts["status"].unique()))
            f_accounts = st.multiselect("Account", sorted(alerts["client_no"].dropna().unique()))
            f_users = st.multiselect("Handled by", sorted(alerts["handled_by"].dropna().unique()))
            f_from = st.date_input("Raised from", value=(alerts["triggered_at"].min()).date())
            f_to = st.date_input("Raised to", value=today.date())
        st.form_submit_button("Apply filters", icon=":material/filter_alt:")

    view = alerts[alerts["status"].isin(f_status)]
    if f_accounts:
        view = view[view["client_no"].isin(f_accounts)]
    if f_users:
        view = view[view["handled_by"].isin(f_users)]
    view = view[(view["triggered_at"].dt.date >= f_from) & (view["triggered_at"].dt.date <= f_to)]

    table = view[[
        "triggered_at", "client_no", "type_label", "message", "status", "handled_by", "action_label",
        "action_remark", "handled_at", "resolved_at",
    ]].rename(columns={
        "triggered_at": "Raised", "client_no": "Account", "type_label": "Type", "message": "Message",
        "status": "Status", "handled_by": "Handled by", "action_label": "Action", "action_remark": "Remark",
        "handled_at": "Handled at", "resolved_at": "Condition cleared",
    })
    st.caption(f"{len(table)} alert(s)")
    st.dataframe(table, hide_index=True)

    with_files = view[view["action_file"].notna()]
    if not with_files.empty:
        st.markdown("**Attachments**")
        for _, row in with_files.iterrows():
            path = DATA_DIR.parent / row["action_file"]
            if path.exists():
                with st.container(horizontal=True, vertical_alignment="center"):
                    st.write(f"{row['client_no']} — {row['type_label']} ({row['handled_by']})")
                    st.download_button(
                        path.name.split("_", 2)[-1], path.read_bytes(), file_name=path.name,
                        icon=":material/download:", key=f"alert_file_{int(row['id'])}",
                    )
