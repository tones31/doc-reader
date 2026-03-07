import streamlit as st
import requests

from dotenv import load_dotenv
import os

load_dotenv()

API_URL = os.getenv("API_URL")

st.title("Document Reading Service")

# Upload document
uploaded_file = st.file_uploader("Upload a document", type=["pdf", "txt"])
if uploaded_file is not None: 
    if uploaded_file.type == "application/pdf":
        response = requests.post(f"{API_URL}/ingest_pdf", files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")})
        st.success(F"PDF stored with ID: {response.json()['id']}")
    else:
        text = uploaded_file.read().decode("utf-8")
        response = requests.post(f"{API_URL}/ingest", json={"text": text})
        st.success(F"Document stored with ID: {response.json()['id']}")

# Ask a question
question = st.text_input("Ask a question any of your uploaded documents")

if st.button("Ask"):
    if question:
        response = requests.post(f"{API_URL}/ask", json={"question": question})
        data = response.json()
        st.subheader("Answer")
        st.write(data["answer"])
        st.subheader("Retrieved Documents")
        for doc in data.get('retrieved_documents', []):
            st.write(f"- {doc}")
    else:
        st.warning("Please enter a question")