"""Who is using the dashboard.

Anyone opening the app is asked for their name first; it is kept in the browser session
and written into every log of that person's actions (remarks, alert handling, settings
changes). It is an honour-system label for the audit trail, not authentication.
"""

from __future__ import annotations

import streamlit as st

_KEY = "username"
_QUERY_KEY = "user"
MAX_LEN = 50


def clean_name(raw: str | None) -> str:
    return " ".join((raw or "").split())[:MAX_LEN]


def current_user() -> str:
    return st.session_state.get(_KEY) or "unknown"


def require_user() -> str:
    """Block the page with a name prompt until a name is entered. Returns the name."""
    name = clean_name(st.session_state.get(_KEY)) or clean_name(st.query_params.get(_QUERY_KEY))
    if name:
        st.session_state[_KEY] = name
        return name

    st.title("NBS account monitor")
    with st.form("identify_user_form", border=True):
        st.markdown("**Who is using the dashboard?**")
        st.caption("Your name is recorded against anything you do here: remarks, alert handling and settings changes.")
        entered = st.text_input("Your name", max_chars=MAX_LEN, placeholder="e.g. Ruoqing")
        if st.form_submit_button("Continue", icon=":material/login:", type="primary"):
            name = clean_name(entered)
            if not name:
                st.error("Please enter your name.")
            else:
                st.session_state[_KEY] = name
                st.query_params[_QUERY_KEY] = name  # lets a browser refresh remember the name
                st.rerun()
    st.stop()


def switch_user_control() -> None:
    """Small 'signed in as' line with a way to change the name."""
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(f"Signed in as **{current_user()}**")
        if st.button("Not you?", type="tertiary", key="switch_user_btn"):
            st.session_state.pop(_KEY, None)
            st.session_state.pop("admin_unlocked", None)
            st.session_state.pop("admin_user", None)
            st.query_params.clear()
            st.rerun()
