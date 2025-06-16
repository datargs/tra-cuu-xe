import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ================== CẤU HÌNH ==================
st.set_page_config(page_title="Tra cứu xe", layout="wide")

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

@st.cache_resource
def load_data():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        dict(st.secrets["gcp_service_account"]), SCOPE
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_url(
        "https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo/edit"
    )
    worksheet = sheet.sheet1
    data = worksheet.get_all_records()
    df = pd.DataFrame(data)
    return df

# ================== TẢI DỮ LIỆU ==================
df = load_data()

# ================== GIAO DIỆN ==================
st.title("🚗 Tra cứu lịch sử bảo dưỡng xe")

# Tạo danh sách các biển số duy nhất
bien_so_list = df['Biển số'].dropna().unique().tolist()
bien_so = st.selectbox("🔍 Chọn biển số xe", bien_so_list)

# ================== HIỂN THỊ DỮ LIỆU ==================
df_xe = df[df['Biển số'] == bien_so]

if not df_xe.empty:
    st.subheader(f"📋 Lịch sử bảo dưỡng của xe {bien_so}")
    st.dataframe(df_xe, use_container_width=True)
else:
    st.warning("Không tìm thấy dữ liệu cho biển số này.")
