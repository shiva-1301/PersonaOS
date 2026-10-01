"""What PersonaOS remembers about you, and deleting it (one memory, or everything)."""

import streamlit as st

from frontend import session, style
from frontend.api_client import ApiClient
from frontend.firebase_auth import AuthError

CONFIRM = "DELETE MY DATA"
STATES = ["active", "stale", "archived", "superseded"]
EXPLAIN = {
    "active": "used in answers",
    "stale": "fading: not used for a while",
    "archived": "kept, but no longer used",
    "superseded": "replaced by something newer",
}


def _memories(api: ApiClient) -> None:
    health = api.memory_health()
    cols = st.columns(4)
    for col, state in zip(cols, STATES, strict=True):
        col.metric(state.capitalize(), health["by_state"].get(state, 0), help=EXPLAIN[state])

    memories = api.memories()
    if not memories:
        st.info('Nothing remembered yet. Chat a little, or say "Remember that …".')
        return
    shown = st.multiselect("Show", STATES, default=["active", "stale"])
    tz = session.user_zone(api)
    rows = [
        {
            "memory": m["text"],
            "state": m["state"],
            "strength": round(m["strength"], 2),
            "used": m["access_count"],
            "last used": session.local_time(m["last_accessed_at"], tz, "%d %b %Y"),
        }
        for m in memories
        if m["state"] in shown
    ]
    st.dataframe(rows, hide_index=True, width="stretch")

    by_label = {f"{m['text'][:80]} ({m['state']})": m["id"] for m in memories}
    with st.form("forget"):
        choice = st.selectbox(
            "Forget one memory", list(by_label), index=None, placeholder="Choose…"
        )
        forget = st.form_submit_button("Forget it") and choice
        if forget and session.attempt(api.delete_memory, by_label[choice]):
            session.flash("Forgotten everywhere it was stored.")
            st.rerun()


def _delete_everything(api: ApiClient) -> None:
    st.subheader("Delete everything")
    st.write(
        "Permanently deletes your memories, documents, chats, goals, tasks and Google "
        "connection (access is revoked at Google). Events already in your Google Calendar stay."
    )
    with st.form("delete-all"):
        typed = st.text_input(f'Type "{CONFIRM}" to confirm')
        also_login = st.checkbox("Also delete my login (I won't be able to sign in with it again)")
        if st.form_submit_button("Delete all my data", type="primary"):
            if typed.strip() != CONFIRM:
                st.error(f'Type exactly "{CONFIRM}".')
                return
            if also_login and not session.firebase().signed_in_recently(session.signed_in()):
                # Checked first, so nothing is deleted unless the login can be deleted too.
                st.error(
                    "To delete your login, Firebase needs a recent sign-in. Sign out, sign in "
                    "again, and do this within a few minutes. Nothing was deleted."
                )
                return
            result = session.attempt(api.delete_all_data)
            if not result:
                return
            notice = "All your PersonaOS data was deleted."
            if also_login:
                try:
                    session.firebase().delete_account(session.signed_in())
                    notice += " Your login was deleted too."
                except AuthError as exc:
                    notice += f" Your login was NOT deleted: {exc.message}"
            session.sign_out(notice)
            st.rerun()


def render() -> None:
    api = session.client()
    style.hero(
        "Memory",
        "What I *remember*.",
        "Facts fade when unused and newer ones replace older ones. Forget anything here.",
        left=True,
    )
    _memories(api)
    st.divider()
    _delete_everything(api)
