"""Shared, hideable alert panel -- mounted once in streamlit_app.py so it
shows on every tab, not just Prop accounts.

Reads from archive_db's alert_log table, which any page can contribute to
via archive_db.sync_alerts(source, current_alerts) -- source is a free-text
tag (e.g. "prop_accounts") so alerts from different tabs stay distinguishable
in one shared history. Currently only the Prop accounts page (breach +
P&L threshold) populates it; other tabs can opt in the same way later.

Re-syncs prop-account alerts before rendering so the panel reflects the
current state even on a tab that doesn't itself compute alerts -- rate
limited to once per TTL window (not on every single click) since it re-reads
MarginNowV2.xls.
"""

from __future__ import annotations

import streamlit as st

from utils import archive_db

_DISPLAY_COLUMNS = {
    "source": "Source",
    "alert_type": "Type",
    "client_no": "Account",
    "message": "Message",
}


@st.cache_data(ttl="30s", show_spinner=False)
def _refresh_prop_alerts() -> int:
    from utils import alert_engine
    from utils.data_loader import DATA_DIR

    return len(alert_engine.sync_prop_alerts(DATA_DIR))


def render() -> None:
    try:
        _refresh_prop_alerts()
    except Exception:  # noqa: BLE001 -- alert refresh must never break page rendering
        pass

    active_count = archive_db.active_alert_count()
    label = f"Alerts ({active_count} active)" if active_count else "Alerts (none active)"
    icon = ":material/notification_important:" if active_count else ":material/notifications:"
    with st.expander(label, icon=icon, expanded=False):
        log_df = archive_db.load_alert_log()
        if log_df.empty:
            st.caption("No alerts logged yet.")
            return

        active = log_df[log_df["resolved_at"].isna()]
        st.markdown(f"**Active ({len(active)})**")
        if active.empty:
            st.caption("No active alerts.")
        else:
            table = active[list(_DISPLAY_COLUMNS) + ["triggered_at"]].rename(
                columns={**_DISPLAY_COLUMNS, "triggered_at": "Since"}
            )
            st.dataframe(table, hide_index=True)

        resolved = log_df[log_df["resolved_at"].notna()].head(200)
        if not resolved.empty:
            st.markdown(f"**History ({len(resolved)} shown)**")
            table = resolved[list(_DISPLAY_COLUMNS) + ["triggered_at", "resolved_at"]].rename(
                columns={**_DISPLAY_COLUMNS, "triggered_at": "Triggered", "resolved_at": "Resolved"}
            )
            st.dataframe(table, hide_index=True)
