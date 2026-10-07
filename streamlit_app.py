import streamlit as st

from utils import alert_ui, identity

st.set_page_config(page_title="NBS account monitor", page_icon=":material/monitoring:", layout="wide")

identity.require_user()  # asks for a name first; everything below is skipped until it is given

page = st.navigation(
    [
        st.Page("app_pages/overview.py", title="Overview", icon=":material/dashboard:", default=True),
        st.Page("app_pages/alert_audit.py", title="Alert audit", icon=":material/notification_important:"),
        st.Page("app_pages/archive_history.py", title="Archive history", icon=":material/database:"),
        st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:"),
    ],
    position="top",
)

identity.switch_user_control()
alert_ui.mount()  # pop-up for new alerts, open-alert banner, and the live watcher
page.run()
