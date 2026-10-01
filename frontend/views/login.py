"""Sign in, create an account, or reset a password (Firebase email/password)."""

import streamlit as st

from frontend import session
from frontend.api_client import ApiError, ApiUnavailable
from frontend.firebase_auth import AuthError


def _after_sign_in(firebase_session) -> None:
    session.start_session(firebase_session)
    try:
        if zone := session.sync_timezone(session.client(), st.context.timezone):
            session.flash(f"Your timezone is set to {zone} (from your browser).")
    except ApiUnavailable as exc:
        session.show_unavailable(exc)
        st.stop()
    except ApiError:
        pass  # the timezone can still be set in the sidebar
    st.rerun()


def render() -> None:
    st.title("PersonaOS")
    st.caption("One Identity. One Memory. Infinite Intelligence.")
    if notice := st.session_state.get(session.NOTICE):
        st.warning(notice)

    try:
        auth = session.firebase()
    except AuthError as exc:
        st.error(exc.message)
        return

    sign_in, sign_up, reset = st.tabs(["Sign in", "Create account", "Forgot password"])
    with sign_in, st.form("sign-in"):
        email = st.text_input("Email", key="signin-email")
        password = st.text_input("Password", type="password", key="signin-password")
        if st.form_submit_button("Sign in", type="primary"):
            try:
                _after_sign_in(auth.sign_in(email, password))
            except AuthError as exc:
                st.error(exc.message)

    with sign_up, st.form("sign-up"):
        email = st.text_input("Email", key="signup-email")
        password = st.text_input(
            "Password", type="password", key="signup-password", help="At least 6 characters."
        )
        repeat = st.text_input("Repeat password", type="password", key="signup-repeat")
        if st.form_submit_button("Create account", type="primary"):
            if password != repeat:
                st.error("The passwords don't match.")
            else:
                try:
                    _after_sign_in(auth.sign_up(email, password))
                except AuthError as exc:
                    st.error(exc.message)

    with reset, st.form("reset"):
        email = st.text_input("Email", key="reset-email")
        if st.form_submit_button("Send reset email"):
            try:
                auth.send_password_reset(email)
                st.success("If that account exists, a reset email is on its way.")
            except AuthError as exc:
                # Don't reveal whether an account exists.
                if exc.reason == "EMAIL_NOT_FOUND":
                    st.success("If that account exists, a reset email is on its way.")
                else:
                    st.error(exc.message)
