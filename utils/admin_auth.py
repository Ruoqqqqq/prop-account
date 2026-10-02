"""Admin-password gate for the Settings page.

The password is never stored in the code or the repo. Set it either as the
NBS_ADMIN_PASSWORD environment variable or as `admin_password` in
.streamlit/secrets.toml. With neither set, settings stay read-only for everyone.
"""

from __future__ import annotations

import hmac
import os

import streamlit as st

_SESSION_KEY = "admin_unlocked"


def _configured_password() -> str | None:
    env = os.environ.get("NBS_ADMIN_PASSWORD")
    if env:
        return env
    try:
        return st.secrets.get("admin_password") or None
    except Exception:  # noqa: BLE001 -- no secrets.toml at all
        return None


def is_admin() -> bool:
    return bool(st.session_state.get(_SESSION_KEY))


def render_unlock() -> bool:
    """Show the unlock / lock control. Returns True if this session may modify settings."""
    password = _configured_password()
    if password is None:
        st.warning(
            "No admin password is configured, so settings are read-only. Set the "
            "`NBS_ADMIN_PASSWORD` environment variable (or `admin_password` in "
            ".streamlit/secrets.toml) and restart the app."
        )
        return False

    if is_admin():
        with st.container(horizontal=True):
            st.success("Admin mode: you can modify settings.", icon=":material/lock_open:")
            if st.button("Lock settings", icon=":material/lock:"):
                st.session_state[_SESSION_KEY] = False
                st.rerun()
        return True

    with st.form("admin_unlock_form", border=True):
        entered = st.text_input("Admin password", type="password", help="Required to modify any setting.")
        if st.form_submit_button("Unlock", icon=":material/lock_open:"):
            if hmac.compare_digest(entered.encode(), password.encode()):
                st.session_state[_SESSION_KEY] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.caption("Settings are visible but read-only until unlocked.")
    return False
