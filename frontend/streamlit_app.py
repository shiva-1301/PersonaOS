"""PersonaOS web UI.

Run locally:  .venv\\Scripts\\streamlit.exe run frontend\\streamlit_app.py
In Docker:    the `frontend` service in docker-compose.yml (http://localhost:8501)
"""

import sys
from pathlib import Path
from zoneinfo import available_timezones

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # `streamlit run` puts only frontend/ on the path
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from frontend import session  # noqa: E402
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


def sidebar() -> None:
    with st.sidebar:
        auth = session.signed_in()
        st.caption(f"Signed in as **{auth.email}**")
        api = session.client()
        try:
            me = session.me(api)
        except ApiError:
            me = None  # the page itself shows what went wrong
        if me:
            with st.expander(f"Timezone: {me['timezone']}"):
                current = me["timezone"] if me["timezone"] in TIMEZONES else "UTC"
                choice = st.selectbox("Your timezone", TIMEZONES, index=TIMEZONES.index(current))
                if choice != me["timezone"] and st.button("Save timezone"):
                    updated = session.attempt(api.update_me, timezone=choice)
                    if updated:
                        st.session_state["me"] = updated
                        session.flash(f"Timezone set to {choice}.")
                        st.rerun()
        if st.button("Sign out"):
            session.sign_out("You signed out.")
            st.rerun()


st.set_page_config(page_title="PersonaOS", page_icon=":material/psychology:", layout="wide")

if session.signed_in() is None:
    login.render()
    st.stop()

sidebar()
pages = [
    st.Page(
        session.page(chat.render),
        title="Chat",
        icon=":material/chat:",
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
st.navigation(pages).run()
