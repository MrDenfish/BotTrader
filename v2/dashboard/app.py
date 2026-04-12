"""BotTrader Dashboard — Streamlit entry point."""

import streamlit as st

st.set_page_config(
    page_title="BotTrader Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

report_page = st.Page("pages/report.py", title="Report", icon="📈", default=True)
health_page = st.Page("pages/health.py", title="Bot Health", icon="🩺")
config_page = st.Page("pages/config_editor.py", title="Config", icon="⚙️")

pg = st.navigation([report_page, health_page, config_page])
pg.run()
