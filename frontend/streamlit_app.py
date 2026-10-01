"""PersonaOS web UI.

Run locally:  .\\scripts\\ui.ps1   (runs from frontend/ so .streamlit/config.toml applies)
In Docker:    the `frontend` service in docker-compose.yml (http://localhost:8501)
"""

import sys
from pathlib import Path
from zoneinfo import available_timezones

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:  # `streamlit run` puts only frontend/ on the path
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from frontend import session, style  # noqa: E402
from frontend.api_client import ApiError  # noqa: E402
from frontend.views import (  # noqa: E402
    chat,
    dashboard,
    documents,
    goals,
    integrations,
    login,
    memory,
)

TIMEZONES = sorted(available_timezones())


def account_menu() -> None:
    """Top right of every page: who is signed in, their timezone, sign out."""
    auth = session.signed_in()
    api = session.client()
    try:
        me = session.me(api)
    except ApiError:
        me = None  # the page itself shows what went wrong
    with st.container(key="account"), st.popover(auth.email, icon=":material/person:"):
        st.caption(f"Signed in as **{auth.email}**")
        if me:
            current = me["timezone"] if me["timezone"] in TIMEZONES else "UTC"
            choice = st.selectbox("Your timezone", TIMEZONES, index=TIMEZONES.index(current))
            if choice != me["timezone"] and st.button("Save timezone"):
                updated = session.attempt(api.update_me, timezone=choice)
                if updated:
                    st.session_state["me"] = updated
                    session.flash(f"Timezone set to {choice}.")
                    st.rerun()
        if st.button("Sign out", type="primary"):
            session.sign_out("You signed out.")
            st.rerun()


st.set_page_config(
    page_title="PersonaOS",
    page_icon=str(HERE / "static" / "logo.svg"),
    layout="wide",
    initial_sidebar_state="collapsed",
)
style.inject()

if session.signed_in() is None:
    login.render()
    st.stop()

st.logo(str(HERE / "static" / "logo.svg"), size="large")
account_menu()
pages = [
    st.Page(
        session.page(chat.render),
        title="Chat",
        icon=":material/chat_bubble:",
        url_path="chat",
        default=True,
    ),
    st.Page(
        session.page(documents.render),
        title="Documents",
        icon=":material/description:",
        url_path="documents",
    ),
    st.Page(
        session.page(goals.render),
        title="Goals & Tasks",
        icon=":material/checklist:",
        url_path="goals",
    ),
    st.Page(
        session.page(dashboard.render),
        title="Dashboard",
        icon=":material/insights:",
        url_path="dashboard",
    ),
    st.Page(
        session.page(memory.render), title="Memory", icon=":material/neurology:", url_path="memory"
    ),
    st.Page(
        session.page(integrations.render),
        title="Integrations",
        icon=":material/calendar_month:",
        url_path="integrations",
    ),
]
st.navigation(pages, position="top").run()
