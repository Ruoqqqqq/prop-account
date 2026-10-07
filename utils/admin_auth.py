"""Admin-password gate for the Settings page.

The password is never stored in the code or the repo. Set it either as the
NBS_ADMIN_PASSWORD environment variable or as `admin_password` in
.streamlit/secrets.toml. With neither set, settings stay read-only for everyone.

Unlocking also asks for a username (any non-empty text is accepted as long as the
admin password is right). That name is what the settings audit trail records for every
change made during the unlocked session.
"""

from __future__ import annotations

import hmac
import os

import streamlit as st

from utils import identity

_SESSION_KEY = "admin_unlocked"
_USER_KEY = "admin_user"


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


def admin_user() -> str:
    """Name to record against settings changes in this unlocked session."""
    return st.session_state.get(_USER_KEY) or identity.current_user()


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
            st.success(f"Admin mode as **{admin_user()}**: changes are logged under this name.", icon=":material/lock_open:")
            if st.button("Lock settings", icon=":material/lock:"):
                st.session_state[_SESSION_KEY] = False
                st.session_state.pop(_USER_KEY, None)
                st.rerun()
        return True

    with st.form("admin_unlock_form", border=True):
        username = st.text_input(
            "Username", value=identity.current_user() if identity.current_user() != "unknown" else "",
            max_chars=identity.MAX_LEN, help="Recorded in the audit trail against every change you make.",
        )
        entered = st.text_input("Admin password", type="password", help="Required to modify any setting.")
        if st.form_submit_button("Unlock", icon=":material/lock_open:"):
            name = identity.clean_name(username)
            if not name:
                st.error("Enter a username so changes can be attributed to you.")
            elif hmac.compare_digest(entered.encode(), password.encode()):
                st.session_state[_SESSION_KEY] = True
                st.session_state[_USER_KEY] = name
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.caption("Settings are visible but read-only until unlocked.")
    return False
