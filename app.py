import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from io import BytesIO

# Thiết lập Google Sheets API
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
client = gspread.authorize(creds)

# Mở Google Sheet
spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

# Đọc dữ liệu từ các sheet
df_xe = pd.DataFrame(spreadsheet.worksheet("Xe").get_all_records())
df_lich = pd.DataFrame(spreadsheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())
df_ls = pd.DataFrame(spreadsheet.worksheet("Lịch sử bảo dưỡng").get_all_records())

# Thiết lập giao diện
st.title("📋 Tra cứu lịch sử và lịch bảo dưỡng xe")

# Biển số chọn ở trên đầu, có tìm kiếm
bien_so = st.selectbox("🔍 Chọn biển số xe", sorted(df_xe["Biển số"].unique()))

# Hiển thị thông tin xe
st.subheader("📌 Thông tin xe")
st.table(df_xe[df_xe["Biển số"] == bien_so])

# Hiển thị lịch bảo dưỡng tiếp theo
st.subheader("📅 Lịch bảo dưỡng tiếp theo")
lich_tiep = df_lich[df_lich["Biển số"] == bien_so]
if not lich_tiep.empty:
    du_kien = lich_tiep.iloc[0]["Dự kiến lần tiếp theo"]
    goi_y = lich_tiep.iloc[0]["Gợi ý nội dung"]
    st.info(f"**Dự kiến lần tiếp theo:** {du_kien}\n\n**Gợi ý nội dung:** {goi_y}")
else:
    st.warning("Chưa có lịch bảo dưỡng tiếp theo")

# Hiển thị lịch sử bảo dưỡng
st.subheader("🧾 Lịch sử bảo dưỡng")

# Lọc theo khoảng thời gian
col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    tu_ngay = st.date_input("📆 Từ ngày", value=None)
with col2:
    den_ngay = st.date_input("📆 Đến ngày", value=None)
with col3:
    if st.button("Xem"):
        if tu_ngay and den_ngay:
            if tu_ngay > den_ngay:
                st.error("❌ Từ ngày phải nhỏ hơn hoặc bằng đến ngày. Vui lòng chọn lại.")
            else:
                df_locs = df_ls[
                    (df_ls["Biển số"] == bien_so) &
                    (pd.to_datetime(df_ls["Ngày"], dayfirst=True) >= pd.to_datetime(tu_ngay)) &
                    (pd.to_datetime(df_ls["Ngày"], dayfirst=True) <= pd.to_datetime(den_ngay))
                ]
                st.dataframe(df_locs, use_container_width=True)
                # Tổng chi phí
                if not df_locs.empty:
                    try:
                        df_locs["Chi phí"] = pd.to_numeric(df_locs["Chi phí"], errors='coerce')
                        tong = df_locs["Chi phí"].sum()
                        st.success(f"💰 **Tổng chi phí:** {tong:,.0f} VND")
                    except:
                        st.warning("Không thể tính tổng chi phí do dữ liệu không hợp lệ.")
        else:
            df_locs = df_ls[df_ls["Biển số"] == bien_so]
            st.dataframe(df_locs, use_container_width=True)
            if not df_locs.empty:
                try:
                    df_locs["Chi phí"] = pd.to_numeric(df_locs["Chi phí"], errors='coerce')
                    tong = df_locs["Chi phí"].sum()
                    st.success(f"💰 **Tổng chi phí:** {tong:,.0f} VND")
                except:
                    st.warning("Không thể tính tổng chi phí do dữ liệu không hợp lệ.")

# Xuất Excel
if st.button("📤 Xuất Excel lịch sử bảo dưỡng"):
    df_export = df_ls[df_ls["Biển số"] == bien_so]
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_export.to_excel(writer, index=False, sheet_name='Lịch sử bảo dưỡng')
    output.seek(0)
    st.download_button(
        label="Tải file Excel",
        data=output,
        file_name=f"Lich_su_bao_duong_{bien_so}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
