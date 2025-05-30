import streamlit as st
import sqlite3
import os
import uuid
from db_utils import load_database_schema, run_query
from ai_agent import generate_sql_query

st.set_page_config(page_title="Text-to-SQL AI", layout="wide")
st.title("🧠 Text-to-SQL AI Agent (Claude 3.5 Haiku)")

# --- File Upload ---
uploaded_file = st.file_uploader(
    "Upload a SQLite database or SQL schema file",
    type=["sqlite", "db", "sql", "txt"],
    help="Accepted: .sqlite, .db, .sql, .txt (max 200MB)"
)

conn = None
unique_db_name = None

if uploaded_file:
    st.success("✅ Database uploaded successfully!")
    unique_db_name = f"temp_db_{uuid.uuid4().hex}.sqlite"
    # Handle .sql/.txt (schema) or .sqlite/.db (binary)
    try:
        if uploaded_file.name.endswith((".sql", ".txt")):
            # Create new SQLite DB and execute schema
            conn = sqlite3.connect(unique_db_name)
            sql_script = uploaded_file.read().decode("utf-8")
            conn.executescript(sql_script)
            st.success("✅ SQL script executed and database created!")
        else:
            # Save uploaded binary DB file
            with open(unique_db_name, "wb") as f:
                f.write(uploaded_file.read())
            conn = sqlite3.connect(unique_db_name)
    except Exception as e:
        st.error(f"❌ Failed to load or create database: {e}")
        st.stop()

    # --- Schema Extraction ---
    schema = load_database_schema(conn)
    st.subheader("Database Schema:")
    st.code(schema, language="markdown")

    # --- User Question ---
    st.subheader("Ask your question:")
    user_question = st.text_input("e.g., List all customer names and emails")

    if user_question:
        with st.spinner("Generating SQL with Claude 3.5 Haiku..."):
            response_type, response_content = generate_sql_query(schema, user_question)
        if response_type == "sql":
            st.subheader("🧠 Generated SQL:")
            st.code(response_content, language="sql")
            try:
                result_df = run_query(conn, response_content)
                st.success("✅ Query executed successfully!")
                st.dataframe(result_df)
            except Exception as e:
                st.error(f"❌ Error executing SQL: {e}")
        else:
            st.subheader("💡 Explanation:")
            st.markdown(response_content)

    # --- Cleanup: Optionally delete temp DB file on rerun/exit ---
    # (Streamlit may rerun scripts, so be careful with file deletion)
else:
    st.info("Upload a SQLite or SQL file to get started.")
