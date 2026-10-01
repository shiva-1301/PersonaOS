"""Google Calendar: connect, see upcoming events, confirm proposed events, disconnect."""

import streamlit as st

from frontend import session
from frontend.api_client import ApiClient, ApiError


def _connect(api: ApiClient, reconnect: bool) -> None:
    if reconnect:
        st.warning(
            "Google access expired or was revoked (in Google's Testing mode it lasts 7 days). "
            "Connect again to keep using your calendar."
        )
    else:
        st.write("Connect Google Calendar to see your schedule and add events you confirm.")
    url = session.attempt(api.google_connect_url)
    if url:
        st.link_button(
            "Reconnect Google Calendar" if reconnect else "Connect Google Calendar",
            url,
            type="primary",
        )
        st.caption(
            "Opens Google in a new tab. Sign in with an account listed as a test user, "
            "allow calendar access, then come back here and press Refresh."
        )
    if st.button("Refresh"):
        st.rerun()


def _connected(api: ApiClient) -> None:
    tz = session.user_zone(api)
    st.success("Google Calendar is connected.")

    proposals = api.proposals()
    if proposals:
        st.subheader("Waiting for your confirmation")
        for p in proposals:
            with st.container(border=True):
                st.markdown(
                    f"**{p['title']}** · {session.local_time(p['start_at'], tz)} to "
                    f"{session.local_time(p['end_at'], tz, '%H:%M')}"
                )
                add, cancel = st.columns(2)
                add_it = add.button("Add to Google Calendar", key=f"ok-{p['id']}", type="primary")
                if add_it and session.attempt(api.confirm_proposal, p["id"]):
                    session.flash(f'"{p["title"]}" was added to your Google Calendar.')
                    st.rerun()
                drop_it = cancel.button("Don't add", key=f"no-{p['id']}")
                if drop_it and session.attempt(api.cancel_proposal, p["id"]):
                    st.rerun()

    st.subheader("Next 7 days")
    try:
        events = api.calendar_events(days=7)
    except ApiError as exc:
        if exc.status not in (409, 503):
            raise
        st.warning(exc.message)
        events = []
    if not events:
        st.info("Nothing on your calendar for the next 7 days.")
    for e in events:
        when = (
            session.local_time(e["start"], tz) if e["start"] and "T" in e["start"] else e["start"]
        )
        line = f"**{e['title']}** · {when}"
        st.markdown(f"[{line}]({e['link']})" if e.get("link") else line)

    st.divider()
    with st.popover("Disconnect Google Calendar"):
        st.write("Revoke PersonaOS's access at Google and delete the stored token?")
        sure = st.button("Yes, disconnect", type="primary")
        if sure and session.attempt(api.google_disconnect):
            session.flash("Google Calendar disconnected.")
            st.rerun()


def render() -> None:
    api = session.client()
    st.title("Integrations")
    st.subheader("Google Calendar")
    status = api.google_status()
    if not status["configured"]:
        st.info(
            "Google Calendar isn't set up on this server (GOOGLE_CLIENT_ID, "
            "GOOGLE_CLIENT_SECRET and TOKEN_ENCRYPTION_KEY in .env)."
        )
    elif status["connected"]:
        _connected(api)
    else:
        _connect(api, reconnect=status["needs_reconnect"])
