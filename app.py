"""One Streamlit app with a page per task.  Started by `python run.py`."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

st.set_page_config(page_title="AI Tech Consultant", page_icon=":material/support_agent:", layout="wide")

pages = st.navigation([
    st.Page("task1_knowledge_brain/ui.py", title="Knowledge Chat", icon=":material/menu_book:", url_path="knowledge"),
    st.Page("task2_inbox_agent/ui.py", title="Inbox Review", icon=":material/mail:", url_path="inbox"),
    st.Page("task3_meeting_to_scope/ui.py", title="Scope Builder", icon=":material/assignment:", url_path="scope"),
])
pages.run()
