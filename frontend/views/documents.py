"""Upload notes (PDF, DOCX, TXT), watch them get processed, summarise, search, delete."""

import streamlit as st

from frontend import session
from frontend.api_client import ApiClient

STATUS = {"processing": "⏳ Processing", "ready": "✅ Ready", "failed": "⚠️ Failed"}


def _upload(api: ApiClient) -> None:
    limit = session.settings().max_upload_mb
    with st.form("upload", clear_on_submit=True):
        file = st.file_uploader(
            f"Add a document (PDF, DOCX or TXT, up to {limit} MB)", type=["pdf", "docx", "txt"]
        )
        if st.form_submit_button("Upload", type="primary") and file is not None:
            if file.size > limit * 1024 * 1024:
                st.error(f"{file.name} is {file.size / 1_048_576:.1f} MB. The limit is {limit} MB.")
                return
            doc = session.attempt(api.upload_document, file.name, file.getvalue(), file.type)
            if doc is not None:
                session.flash(f"{file.name} uploaded. It's being processed now.")
                st.rerun()


def _document(api: ApiClient, doc: dict) -> None:
    with st.container(border=True):
        top, actions = st.columns([3, 2], vertical_alignment="center")
        top.markdown(f"**{doc['filename']}**")
        detail = STATUS.get(doc["status"], doc["status"])
        if doc["status"] == "ready":
            detail += f" · {doc['chunk_count']} chunks"
        top.caption(detail)
        if doc["status"] == "failed" and doc.get("error"):
            st.warning(doc["error"])
        summarise, delete = actions.columns(2)
        if summarise.button(
            "Summarise", key=f"sum-{doc['id']}", disabled=doc["status"] != "ready", width="stretch"
        ):
            with st.spinner("Summarising with the local model…"):
                result = session.attempt(api.summarize_document, doc["id"])
            if result is not None:
                doc["summary"] = result["summary"]
        with delete.popover("Delete", width="stretch"):
            st.write(f"Delete **{doc['filename']}** and everything extracted from it?")
            sure = st.button("Yes, delete it", key=f"del-{doc['id']}", type="primary")
            if sure and session.attempt(api.delete_document, doc["id"]):
                session.flash(f"{doc['filename']} was deleted.")
                st.rerun()
        if doc.get("summary"):
            with st.expander("Summary", expanded=True):
                st.markdown(doc["summary"])


def _list(api: ApiClient) -> None:
    docs = api.documents()
    if not docs:
        st.info("No documents yet. Upload your notes above and ask about them in Chat.")
        return
    for doc in docs:
        _document(api, doc)
    processing = any(d["status"] == "processing" for d in docs)
    if st.session_state.get("docs_processing") and not processing:
        st.session_state["docs_processing"] = False
        st.rerun(scope="app")  # all done: stop the automatic refresh
    st.session_state["docs_processing"] = processing


def _search(api: ApiClient) -> None:
    query = st.text_input("Search your documents", placeholder="e.g. what is entropy?")
    if not query:
        return
    hits = session.attempt(api.search_documents, query)
    if hits is None:
        return
    if not hits:
        st.info("Nothing in your documents matches that.")
    for hit in hits:
        with st.container(border=True):
            part = hit["chunk_index"] + 1
            st.caption(f"{hit['filename']} · part {part} · relevance {hit['relevance']:.2f}")
            st.markdown(hit["text"][:800] + ("…" if len(hit["text"]) > 800 else ""))


def render() -> None:
    api = session.client()
    st.title("Documents")
    _upload(api)
    processing = any(d["status"] == "processing" for d in api.documents())
    st.session_state["docs_processing"] = processing
    # While something is processing, refresh just this list every few seconds. The
    # guard handles a 401 or an outage during those partial reruns too.
    st.fragment(session.page(lambda: _list(api)), run_every=3 if processing else None)()
    st.divider()
    _search(api)
