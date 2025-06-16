import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Cấu hình giao diện
st.set_page_config(page_title="Tra cứu xe", layout="wide")
st.title("📋 Tra cứu bảo dưỡng xe")

# Đọc thông tin đăng nhập từ Streamlit secrets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = st.secrets["gcp_service_account"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# Đọc Google Sheet
sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

df_xe = pd.DataFrame(sheet.worksheet("Xe").get_all_records())
df_bd = pd.DataFrame(sheet.worksheet("Bảo dưỡng").get_all_records())
df_next = pd.DataFrame(sheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())

# Giao diện người dùng
bien_so = st.sidebar.selectbox("Chọn biển số", df_xe["Biển số"].unique())
xe = df_xe[df_xe["Biển số"] == bien_so].iloc[0]

st.subheader("🧾 Thông tin xe")
st.markdown(f"- **Loại xe**: {xe['Loại xe']}")
st.markdown(f"- **Năm sản xuất**: {xe['Năm sản xuất']}")
st.markdown(f"- **Trạng thái**: {xe['Trạng thái']}")

st.subheader("🛠 Lịch sử bảo dưỡng")
lich_su = df_bd[df_bd["Biển số"] == bien_so]
st.dataframe(lich_su)

st.subheader("🕒 Bảo dưỡng tiếp theo")
next_bd = df_next[df_next["Biển số"] == bien_so]
if not next_bd.empty:
    st.markdown(f"- **Dự kiến**: {next_bd.iloc[0]['Dự kiến lần tiếp theo']}")
    st.markdown(f"- **Gợi ý nội dung**: {next_bd.iloc[0]['Gợi ý nội dung']}")
else:
    st.info("Chưa có lịch bảo dưỡng tiếp theo.")
