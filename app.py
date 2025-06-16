import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection

st.set_page_config(page_title="Tra cứu xe", layout="wide")
st.title("📋 Tra cứu bảo dưỡng xe")

SHEET_URL = "https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo"

conn = st.connection("gsheets", type=GSheetsConnection)
df_xe      = conn.read(spreadsheet=SHEET_URL, worksheet="Xe")
df_bd      = conn.read(spreadsheet=SHEET_URL, worksheet="Bảo dưỡng")
df_bd_next = conn.read(spreadsheet=SHEET_URL, worksheet="Lịch bảo dưỡng tiếp theo")

bien_so = st.sidebar.selectbox("Chọn biển số xe", df_xe["Biển số"].unique())
xe = df_xe[df_xe["Biển số"] == bien_so].iloc[0]

st.header(f"Xe: {bien_so}")
st.markdown(f"- **Loại xe**: {xe['Loại xe']}")
st.markdown(f"- **Năm sản xuất**: {xe['Năm sản xuất']}")
st.markdown(f"- **Trạng thái**: {xe['Trạng thái']}")

st.subheader("🔧 Lịch sử bảo dưỡng")
lich_su = df_bd[df_bd["Biển số"] == bien_so].sort_values("Ngày bảo dưỡng", ascending=False)
st.dataframe(lich_su[["Ngày bảo dưỡng", "Nội dung", "Chi phí"]])

st.subheader("🕒 Bảo dưỡng tiếp theo")
next_ = df_bd_next[df_bd_next["Biển số"] == bien_so]
if not next_.empty:
    ns = next_.iloc[0]
    st.markdown(f"- **Dự kiến**: {ns['Dự kiến lần tiếp theo']}")
    st.markdown(f"- **Nội dung**: {ns['Gợi ý nội dung']}")
else:
    st.
