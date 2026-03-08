import streamlit as st
import requests

from dotenv import load_dotenv
import os

load_dotenv()

API_URL = os.getenv("API_URL")
PAGE_SIZE = 10

st.set_page_config(page_title="Resume Search", page_icon="📄", layout="wide")

# Centered: side margins + wide middle (header and content same width)
_col_left, col_main, _col_right = st.columns([1, 5, 1])
with col_main:
    st.title("Resume Search")
    tab_ask, tab_resumes, tab_settings = st.tabs(["Ask", "Resumes", "Settings"])

# --- Ask tab: full-width input bar + button inline, then results ---
with tab_ask:
    if "searching" not in st.session_state:
        st.session_state.searching = False
    if "pending_question" not in st.session_state:
        st.session_state.pending_question = ""
    if "search_results" not in st.session_state:
        st.session_state.search_results = None

    st.markdown("**Ask a question** about your candidates and find the best match.")
    st.caption("Example: *Who has the most Python experience?* or *Find candidates who know DevOps.*")

    with st.form("ask_form", clear_on_submit=False):
        input_col, btn_col = st.columns([6, 1])
        with input_col:
            question = st.text_input(
                "Ask about candidates",
                value=st.session_state.pending_question if st.session_state.searching else None,
                placeholder="e.g. Who is the best candidate for Python?",
                disabled=st.session_state.searching,
                key="question_input",
                label_visibility="collapsed",
            )
        with btn_col:
            submit = st.form_submit_button("Find")

    if submit and question:
        st.session_state.pending_question = question
        st.session_state.searching = True
        st.rerun()

    if st.session_state.searching:
        with st.spinner("Finding candidates…"):
            response = requests.post(f"{API_URL}/ask", json={"question": st.session_state.pending_question})
            response.raise_for_status()
            data = response.json()
        st.session_state.search_results = data
        st.session_state.searching = False
        st.rerun()

    if st.session_state.search_results is not None:
        data = st.session_state.search_results
        st.divider()
        st.subheader("Answer")
        st.write(data["answer"])
        st.divider()
        st.subheader("Top candidates")
        col1, col2, col3 = st.columns([3, 1, 2])
        with col1:
            st.markdown("**Document**")
        with col2:
            st.markdown("**Relevance**")
        with col3:
            st.markdown("**Download**")
        for c in data.get("ranked_candidates", []):
            name = c.get("filename", c["id"])
            score = c["score"]
            col1, col2, col3 = st.columns([3, 1, 2])
            with col1:
                st.write(name)
            with col2:
                st.write(f"{int(round(score * 100))}%" if isinstance(score, (int, float)) and 0 <= score <= 1 else score)
            with col3:
                if c.get("filename", "").lower().endswith(".pdf"):
                    download_url = f"{API_URL}/documents/download?filename={requests.utils.quote(c['filename'])}"
                    st.link_button("Download PDF", download_url)
                else:
                    st.write("—")
        with st.expander("📎 Relevant excerpts from resumes"):
            for doc in data.get("retrieved_documents", []):
                st.write(doc)
    elif submit and not question:
        st.warning("Please enter a question")

# --- Resumes tab: upload + table of all resumes with pagination ---
with tab_resumes:
    st.subheader("Upload resumes")
    st.caption("Add PDF or text resumes. They’ll be indexed for search and appear in the list below.")
    with st.form("upload_form"):
        uploaded_files = st.file_uploader("Upload resumes", type=["pdf", "txt"], accept_multiple_files=True)
        upload_clicked = st.form_submit_button("Upload")
    if upload_clicked and uploaded_files:
        n = len(uploaded_files)
        progress_bar = st.progress(0.0)
        status = st.empty()
        results = []
        for i, uploaded_file in enumerate(uploaded_files):
            status.caption(f"Uploading {i + 1}/{n}: {uploaded_file.name}")
            try:
                if uploaded_file.type == "application/pdf":
                    response = requests.post(
                        f"{API_URL}/ingest_pdf",
                        files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")},
                    )
                    results.append({"File name": uploaded_file.name, "Type": "PDF", "Status": "Stored"})
                else:
                    text = uploaded_file.read().decode("utf-8")
                    response = requests.post(f"{API_URL}/ingest", json={"text": text})
                    results.append({"File name": uploaded_file.name, "Type": "Text", "Status": "Stored"})
            except Exception as e:
                results.append({"File name": uploaded_file.name, "Type": "PDF" if uploaded_file.type == "application/pdf" else "Text", "Status": f"Error: {e}"})
            progress_bar.progress((i + 1) / n)
        status.empty()
        progress_bar.empty()
        st.dataframe(results, use_container_width=True, hide_index=True)
        ok = sum(1 for r in results if r["Status"] == "Stored")
        if ok == n:
            st.success(f"{n} file(s) uploaded.")
        elif ok:
            st.warning(f"{ok} of {n} file(s) uploaded; {n - ok} failed.")
        else:
            st.error("Upload failed for all files.")
        # Invalidate document list cache so it refetches and shows new uploads
        if "document_list" in st.session_state:
            del st.session_state["document_list"]

    st.divider()
    st.subheader("Document library")
    st.caption("List of all uploaded resumes. Use the Ask tab to search by question.")
    col_cap, col_btn = st.columns([4, 1])
    with col_btn:
        if st.button("Refresh list", key="refresh_doc_list"):
            if "document_list" in st.session_state:
                del st.session_state["document_list"]
            st.rerun()

    # Use cached list if we have it (avoids refetch on every rerun, e.g. when clicking checkbox in Settings)
    if "document_list" not in st.session_state:
        try:
            with st.spinner("Loading document library…"):
                list_resp = requests.get(f"{API_URL}/documents/list")
                list_resp.raise_for_status()
                st.session_state.document_list = list_resp.json().get("documents", [])
        except Exception as e:
            st.session_state.document_list = []
            st.warning(f"Could not load document list: {e}")
    docs = st.session_state.document_list

    if "resumes_page" not in st.session_state:
        st.session_state.resumes_page = 0
    total_pages = max(1, (len(docs) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(st.session_state.resumes_page, total_pages - 1))
    start = page * PAGE_SIZE
    page_docs = docs[start : start + PAGE_SIZE]

    if not page_docs:
        st.info("No resumes yet. Upload some above.")
    else:
        col1, col2, col3 = st.columns([3, 1, 2])
        with col1:
            st.markdown("**Name**")
        with col2:
            st.markdown("**Download**")
        with col3:
            st.write("")
        for i, d in enumerate(page_docs):
            name = d.get("name", "")
            col1, col2, col3 = st.columns([3, 1, 2])
            with col1:
                st.write(name)
            with col2:
                if name.lower().endswith(".pdf"):
                    download_url = f"{API_URL}/documents/download?filename={requests.utils.quote(name)}"
                    st.link_button("Download", download_url)
                else:
                    st.write("—")
            with col3:
                st.write("")
        st.caption(f"Page {page + 1} of {total_pages} ({len(docs)} total)")
        prev_col, mid, next_col = st.columns([1, 2, 1])
        with prev_col:
            if st.button("Previous", disabled=(page == 0), key="resumes_prev"):
                st.session_state.resumes_page = page - 1
                st.rerun()
        with next_col:
            if st.button("Next", disabled=(page >= total_pages - 1), key="resumes_next"):
                st.session_state.resumes_page = page + 1
                st.rerun()

# --- Settings tab: danger zone ---
with tab_settings:
    st.caption("App configuration and destructive actions.")
    with st.expander("⚠️ Danger zone"):
        if st.checkbox("I want to reset the resume database"):
            if st.button("Reset database"):
                response = requests.post(f"{API_URL}/wipe")
                st.success("Resume database reset.")
                if "document_list" in st.session_state:
                    del st.session_state["document_list"]
