"""Chat with the assistant: streamed replies, sources, tools used, calendar proposals."""

import streamlit as st

from frontend import session
from frontend.api_client import ApiClient, ApiError, ApiUnavailable, Unauthorized

CURRENT = "chat_session_id"
PENDING = "chat_pending_confirmations"
TOOL_LABELS = {
    "search_documents": "searched your notes",
    "list_documents": "listed your documents",
    "summarize_document": "summarised a document",
    "create_goal": "created a goal",
    "list_goals": "looked at your goals",
    "add_task": "added a task",
    "update_task": "updated a task",
    "list_tasks": "looked at your tasks",
    "generate_study_plan": "made a study plan",
    "remember_explicit": "saved a memory",
    "list_calendar_events": "checked your calendar",
    "create_calendar_event": "proposed a calendar event",
    "confirm_calendar_event": "added a calendar event",
}


def _session_picker(api: ApiClient) -> None:
    sessions = api.chat_sessions()
    options = [None] + [s["id"] for s in sessions]
    titles = {s["id"]: s["title"] or "Untitled chat" for s in sessions}
    current = st.session_state.get(CURRENT)
    if current not in options:
        current = None
    left, right = st.columns([4, 1], vertical_alignment="bottom")
    choice = left.selectbox(
        "Conversation",
        options,
        index=options.index(current),
        format_func=lambda sid: "New chat" if sid is None else titles[sid],
    )
    if right.button("New chat", width="stretch"):
        choice = None
    if choice != st.session_state.get(CURRENT):
        st.session_state[CURRENT] = choice
        st.session_state.pop(PENDING, None)
        st.rerun()


def _details(reply: dict) -> None:
    notes = []
    if reply.get("tools_used"):
        done = dict.fromkeys(TOOL_LABELS.get(t, t) for t in reply["tools_used"])
        notes.append("I " + ", ".join(done) + ".")
    if reply.get("memories_used"):
        notes.append(f"Used {reply['memories_used']} memories about you.")
    if notes:
        st.caption(" ".join(notes))
    if reply.get("sources"):
        files = dict.fromkeys(s["filename"] for s in reply["sources"])
        st.caption("Sources: " + ", ".join(files))


def _pending(api: ApiClient) -> None:
    tz = session.user_zone(api)
    proposals = st.session_state.get(PENDING) or []
    for p in proposals:
        with st.container(border=True):
            start = session.local_time(p["start_at"], tz)
            end = session.local_time(p["end_at"], tz, "%H:%M")
            st.markdown(f"**Calendar event proposed:** {p['title']}, {start} to {end}")
            st.caption('Nothing is added yet. Reply "yes" in this chat, or use the buttons.')
            add, cancel = st.columns(2)
            pid = p["proposal_id"]
            add_it = add.button("Add to Google Calendar", key=f"confirm-{pid}", type="primary")
            if add_it and session.attempt(api.confirm_proposal, pid):
                session.flash(f'"{p["title"]}" was added to your Google Calendar.')
                st.session_state.pop(PENDING, None)
                st.rerun()
            drop_it = cancel.button("Don't add", key=f"cancel-{pid}")
            if drop_it and session.attempt(api.cancel_proposal, pid):
                st.session_state.pop(PENDING, None)
                st.rerun()


def _send(api: ApiClient, message: str) -> None:
    with st.chat_message("user"):
        st.markdown(message)
    with st.chat_message("assistant"):
        status = st.empty()
        body = st.empty()
        text, reply = "", None
        try:
            for event in api.chat_stream(message, st.session_state.get(CURRENT)):
                if event.event == "token":
                    text += event.data.get("text", "")
                    body.markdown(text + " ▌")
                elif event.event == "reset":
                    text = ""  # the model went on to use a tool: discard the draft
                    body.empty()
                elif event.event == "tool":
                    label = TOOL_LABELS.get(event.data.get("tool"), event.data.get("tool"))
                    status.caption(
                        f"Working… {label}" + ("" if event.data.get("ok") else " (failed)")
                    )
                elif event.event == "done":
                    reply = event.data
        except (Unauthorized, ApiUnavailable):
            raise
        except ApiError as exc:
            status.empty()
            body.error(exc.message)
            return
        status.empty()
        if reply is None:
            body.error("The reply was cut off. Please try again.")
            return
        body.markdown(reply["reply"])
        _details(reply)
    st.session_state[CURRENT] = reply["session_id"]
    st.session_state[PENDING] = reply.get("pending_confirmations") or []
    if st.session_state[PENDING]:
        st.rerun()  # show the confirmation buttons below the conversation


def render() -> None:
    api = session.client()
    st.title("Chat")
    _session_picker(api)

    current = st.session_state.get(CURRENT)
    if current:
        for m in api.chat_session(current)["messages"]:
            if m["role"] in ("user", "assistant"):
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])
    else:
        st.info(
            "Ask anything. PersonaOS remembers what matters about you, searches your "
            "documents, and can manage goals, tasks and your calendar."
        )
    _pending(api)

    if message := st.chat_input("Message PersonaOS"):
        _send(api, message)
    st.caption(
        "New memories are saved in the background a few seconds after each reply "
        "(see the Memory page)."
    )
