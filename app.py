import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import io

# Cấu hình kết nối Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials_dict = st.secrets["gcp_service_account"]
credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
client = gspread.authorize(credentials)

# Lấy dữ liệu từ Google Sheets
spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")
df_xe = pd.DataFrame(spreadsheet.worksheet("Xe").get_all_records())
df_ls = pd.DataFrame(spreadsheet.worksheet("Lịch sử bảo dưỡng").get_all_records())
df_next = pd.DataFrame(spreadsheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())

st.set_page_config(layout="wide")
st.title("🚗 Tra cứu lịch sử bảo dưỡng xe")

# Giao diện chọn biển số nằm trên cùng, có tìm kiếm
selected_plate = st.selectbox("Chọn biển số xe", options=df_xe["Biển số"].unique(), index=None)

# Khung lọc theo ngày
col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    from_date = st.date_input("Từ ngày", value=None)
with col2:
    to_date = st.date_input("Đến ngày", value=None)
with col3:
    xem = st.button("🔍 Xem")

if selected_plate:
    # Hiển thị thông tin xe từ bảng "Xe"
    info = df_xe[df_xe["Biển số"] == selected_plate]
    st.subheader("📄 Thông tin xe")
    st.table(info)

    # Hiển thị lịch bảo dưỡng tiếp theo
    next_bd = df_next[df_next["Biển số"] == selected_plate]
    st.subheader("🛠️ Lịch bảo dưỡng tiếp theo")
    if not next_bd.empty:
        st.table(next_bd)
    else:
        st.info("Chưa có lịch bảo dưỡng tiếp theo.")

    # Lọc và hiển thị lịch sử bảo dưỡng
    df_filtered = df_ls[df_ls["Biển số"] == selected_plate].copy()
    df_filtered["Ngày"] = pd.to_datetime(df_filtered["Ngày"], dayfirst=True, errors="coerce")

    if xem and from_date and to_date:
        if from_date > to_date:
            st.error("❌ 'Từ ngày' phải nhỏ hơn hoặc bằng 'Đến ngày'. Vui lòng chọn lại.")
        else:
            df_filtered = df_filtered[
                (df_filtered["Ngày"] >= pd.to_datetime(from_date)) &
                (df_filtered["Ngày"] <= pd.to_datetime(to_date))
            ]

    st.subheader("📜 Lịch sử bảo dưỡng")
    st.dataframe(df_filtered, use_container_width=True)

    # Tính tổng chi phí
    if "Chi phí" in df_filtered.columns:
        df_filtered["Chi phí số"] = pd.to_numeric(df_filtered["Chi phí"], errors="coerce")
        total_cost = df_filtered["Chi phí số"].sum()
        st.markdown(f"**💰 Tổng chi phí: {total_cost:,.0f} VND**")

    # Xuất Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_filtered.drop(columns=["Chi phí số"], errors="ignore").to_excel(writer, index=False, sheet_name="Lịch sử")
    st.download_button(
        label="📥 Tải Excel lịch sử bảo dưỡng",
        data=output.getvalue(),
        file_name=f"lich_su_bao_duong_{selected_plate}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
