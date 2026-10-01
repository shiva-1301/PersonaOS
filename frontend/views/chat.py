"""Chat with the assistant: streamed replies, sources, tools used, calendar proposals."""

import streamlit as st

from frontend import session
from frontend.api_client import ApiClient, ApiError, ApiUnavailable, Unauthorized

CURRENT = "chat_session_id"
PENDING = "chat_pending_confirmations"
LAST = "chat_last_reply"  # details of the latest reply, shown under it after the redraw
# tool -> (what was done, what couldn't be done)
TOOL_LABELS = {
    "search_documents": ("searched your notes", "search your notes"),
    "list_documents": ("listed your documents", "list your documents"),
    "summarize_document": ("summarised a document", "summarise the document"),
    "create_goal": ("created a goal", "create the goal"),
    "list_goals": ("looked at your goals", "look at your goals"),
    "add_task": ("added a task", "add the task"),
    "update_task": ("updated a task", "update the task"),
    "list_tasks": ("looked at your tasks", "look at your tasks"),
    "generate_study_plan": ("made a study plan", "make the study plan"),
    "remember_explicit": ("saved a memory", "save that memory"),
    "list_calendar_events": ("checked your calendar", "check your calendar"),
    "create_calendar_event": ("proposed a calendar event", "propose the calendar event"),
    "confirm_calendar_event": ("added a calendar event", "add the calendar event"),
}


def _label(tool: str, ok: bool) -> str:
    done, failed = TOOL_LABELS.get(tool, (f"used {tool}", f"use {tool}"))
    return done if ok else f"couldn't {failed}"


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
        st.session_state.pop(LAST, None)
        st.rerun()


def _details(reply: dict) -> None:
    """What the assistant did for this reply. Failed tools are said to have failed: a
    failed call changed nothing, so it must never read as done."""
    notes = []
    results = reply.get("tool_results") or [
        {"tool": t, "ok": True} for t in reply.get("tools_used", [])
    ]
    if results:
        said = dict.fromkeys(_label(r["tool"], r["ok"]) for r in results)
        notes.append("I " + ", ".join(said) + ".")
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
                    label = _label(event.data.get("tool", ""), bool(event.data.get("ok")))
                    status.caption(f"Working… {label}")
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
            body.error(
                "The connection dropped before the reply arrived. The reply is still being "
                "saved; open the conversation again in a moment."
            )
            return
    st.session_state[CURRENT] = reply["session_id"]
    st.session_state[PENDING] = reply.get("pending_confirmations") or []
    st.session_state[LAST] = {"message_id": reply["message_id"], "reply": reply}
    # Redraw from the saved conversation: no half-replaced elements from the stream.
    st.rerun()


def render() -> None:
    api = session.client()
    st.title("Chat")
    _session_picker(api)

    current = st.session_state.get(CURRENT)
    if current:
        last = st.session_state.get(LAST) or {}
        asked = None  # the user message the next assistant message answers
        for m in api.chat_session(current)["messages"]:
            if m["role"] == "user":
                asked = m["id"]
            if m["role"] in ("user", "assistant"):
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])
                    if m["role"] == "assistant" and asked == last.get("message_id"):
                        _details(last["reply"])
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
