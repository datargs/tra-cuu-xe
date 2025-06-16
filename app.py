import streamlit as st
import pandas as pd
import gspread
import json
from oauth2client.service_account import ServiceAccountCredentials

# ==== CẤU HÌNH GOOGLE SHEETS ====
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

@st.cache_resource
def load_data():
    # Lấy thông tin từ st.secrets
    creds_dict = st.secrets["gcp_service_account"]
    creds_json = json.loads(json.dumps(creds_dict))  # Chuyển sang JSON string nếu cần

    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, SCOPE)
    client = gspread.authorize(creds)
    sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo/edit")
    worksheet = sheet.sheet1
    data = worksheet.get_all_records()
    df = pd.DataFrame(data)
    return df

# ==== GIAO DIỆN ====
st.set_page_config(page_title="Tra cứu bảo dưỡng xe", layout="wide")
st.title("📋 Tra cứu lịch sử & lịch bảo dưỡng xe")

df = load_data()

# Danh sách biển số duy nhất
bien_so_list = sorted(df["Biển số"].unique())

# Chọn biển số ngay trên đầu
bien_so = st.selectbox("🔍 Chọn biển số xe để tra cứu:", bien_so_list)

# ==== HIỂN THỊ KẾT QUẢ ====
if bien_so:
    df_selected = df[df["Biển số"] == bien_so]

    st.subheader(f"📅 Lịch bảo dưỡng tiếp theo cho xe {bien_so}")
    next_maint = df_selected["Bảo dưỡng tiếp theo"].dropna().unique()
    if len(next_maint) > 0:
        st.info(f"🔧 {next_maint[0]}")
    else:
        st.warning("Chưa có thông tin bảo dưỡng tiếp theo.")

    st.subheader("📚 Lịch sử bảo dưỡng, sửa chữa:")
    st.dataframe(df_selected.sort_values(by="Ngày", ascending=False), use_container_width=True)
