"""Sign in, create an account, or reset a password (Firebase email/password)."""

import streamlit as st

from frontend import session, style
from frontend.api_client import ApiError, ApiUnavailable
from frontend.firebase_auth import AuthError

# Beside the sign-in card, clear of the headline (positions relative to the card's top).
FLOATING = [
    ("✈  Plan a study week", "top:1.5rem; left:5%", "coral"),
    ("🔔  Remind me about my exam", "top:4.5rem; right:4%", "blue"),
    ("📄  Summarise my notes", "top:13rem; left:9%", ""),
    ("★  5-day streak", "top:16.5rem; right:10%", ""),
]


def _after_sign_in(firebase_session) -> None:
    session.start_session(firebase_session)
    try:
        if zone := session.sync_timezone(session.client(), st.context.timezone):
            session.flash(f"Your timezone is set to {zone} (from your browser).")
    except ApiUnavailable as exc:
        session.show_unavailable(exc)
        st.stop()
    except ApiError:
        pass  # the timezone can still be set in the account menu
    st.rerun()


def render() -> None:
    st.logo(str(session.STATIC / "logo.svg"), size="large")
    style.hero(
        "PersonaOS · your personal AI",
        "One identity. One *memory*.",
        "An assistant that remembers what matters to you, reads your notes and plans your time.",
    )
    style.floating_labels(FLOATING)

    _, middle, _ = st.columns([1, 1.3, 1])
    with middle, st.container(key="auth"):
        if notice := st.session_state.get(session.NOTICE):
            st.warning(notice)
        try:
            auth = session.firebase()
        except AuthError as exc:
            st.error(exc.message)
            return

        sign_in, sign_up, reset = st.tabs(["Sign in", "Create account", "Forgot password"])
        with sign_in, st.form("sign-in", border=False):
            email = st.text_input("Email", key="signin-email")
            password = st.text_input("Password", type="password", key="signin-password")
            if st.form_submit_button("Sign in", type="primary", width="stretch"):
                try:
                    _after_sign_in(auth.sign_in(email, password))
                except AuthError as exc:
                    st.error(exc.message)

        with sign_up, st.form("sign-up", border=False):
            email = st.text_input("Email", key="signup-email")
            password = st.text_input(
                "Password", type="password", key="signup-password", help="At least 6 characters."
            )
            repeat = st.text_input("Repeat password", type="password", key="signup-repeat")
            if st.form_submit_button("Create account", type="primary", width="stretch"):
                if password != repeat:
                    st.error("The passwords don't match.")
                else:
                    try:
                        _after_sign_in(auth.sign_up(email, password))
                    except AuthError as exc:
                        st.error(exc.message)

        with reset, st.form("reset", border=False):
            email = st.text_input("Email", key="reset-email")
            if st.form_submit_button("Send reset email", width="stretch"):
                try:
                    auth.send_password_reset(email)
                    st.success("If that account exists, a reset email is on its way.")
                except AuthError as exc:
                    # Don't reveal whether an account exists.
                    if exc.reason == "EMAIL_NOT_FOUND":
                        st.success("If that account exists, a reset email is on its way.")
                    else:
                        st.error(exc.message)
        st.caption("Sign-in is handled by Firebase; everything else stays in PersonaOS.")
