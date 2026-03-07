import streamlit as st
import requests

from dotenv import load_dotenv
import os

load_dotenv()

API_URL = os.getenv("API_URL")

st.set_page_config(page_title="Resume Search Engine")
st.title("Resume Search Engine")

# Reset resume database
with st.expander("Danger zone"):
    if st.checkbox("I want to reset the resume database"):
        if st.button("Reset database"):
            response = requests.post(f"{API_URL}/wipe")
            st.success("Resume database reset.")

# Upload documents (form ensures upload only runs when Upload is clicked, not on other button clicks)
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

# Ask about candidates (single combined flow)
if "searching" not in st.session_state:
    st.session_state.searching = False
if "pending_question" not in st.session_state:
    st.session_state.pending_question = ""
if "search_results" not in st.session_state:
    st.session_state.search_results = None

# Always show input and button; disable both while searching
question = st.text_input(
    "Ask about candidates",
    value=st.session_state.pending_question if st.session_state.searching else None,
    placeholder="e.g. Who is the best candidate for Python? Who knows the most about DevOps?",
    disabled=st.session_state.searching,
    key="question_input"
)

find_clicked = st.button("Find candidates", disabled=st.session_state.searching)

if find_clicked and question:
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
    st.subheader("Answer")
    st.write(data["answer"])
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
    with st.expander("Relevant excerpts"):
        for doc in data.get("retrieved_documents", []):
            st.write(doc)
elif find_clicked and not question:
    st.warning("Please enter a question")