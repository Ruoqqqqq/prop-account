import streamlit as st

from utils import alert_panel

st.set_page_config(page_title="NBS account monitor", page_icon=":material/monitoring:", layout="wide")

page = st.navigation(
    [
        st.Page("app_pages/prop_monitor.py", title="Prop accounts", icon=":material/candlestick_chart:"),
        st.Page("app_pages/archive_history.py", title="Archive history", icon=":material/database:"),
        st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:"),
    ],
    position="top",
)

alert_panel.render()
page.run()
