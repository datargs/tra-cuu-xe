import streamlit as st
import pandas as pd
import gspread
from datetime import datetime
from google.oauth2.service_account import Credentials
from io import BytesIO

# ===== KẾT NỐI GOOGLE SHEET =====
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
credentials = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"], scopes=SCOPES
)

gc = gspread.authorize(credentials)
spreadsheet = gc.open_by_url("https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo/edit?usp=sharing")

# Đọc các sheet
df_xe = pd.DataFrame(spreadsheet.worksheet("Xe").get_all_records())
df_bdt = pd.DataFrame(spreadsheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())
df_ls = pd.DataFrame(spreadsheet.worksheet("Lịch sử bảo dưỡng").get_all_records())

# Chuyển ngày về định dạng datetime
df_ls["Ngày"] = pd.to_datetime(df_ls["Ngày"], dayfirst=True, errors="coerce")
df_bdt["Ngày"] = pd.to_datetime(df_bdt["Ngày"], dayfirst=True, errors="coerce")

st.set_page_config(page_title="Tra cứu bảo dưỡng xe", layout="wide")
st.title("🚗 Tra cứu bảo dưỡng xe")

# ==== CHỌN BIỂN SỐ ====
bien_so_list = sorted(df_xe["Biển số"].unique())
bien_so = st.selectbox("🔍 Chọn biển số xe", options=bien_so_list)

if bien_so:
    st.success(f"✅ Đã chọn: {bien_so}")

    # ==== THÔNG TIN XE ====
    st.subheader("📄 Thông tin xe")
    st.dataframe(df_xe[df_xe["Biển số"] == bien_so], use_container_width=True)

    # ==== LỊCH BẢO DƯỠNG TIẾP THEO ====
    st.subheader("🛠️ Lịch bảo dưỡng tiếp theo")
    bdt_row = df_bdt[df_bdt["Biển số"] == bien_so]
    if not bdt_row.empty:
        st.dataframe(bdt_row, use_container_width=True)
    else:
        st.info("🚫 Chưa có lịch bảo dưỡng tiếp theo.")

    # ==== LỊCH SỬ BẢO DƯỠNG ====
    st.subheader("📚 Lịch sử bảo dưỡng")

    col1, col2, col3 = st.columns(3)
    with col1:
        tu_ngay = st.date_input("📅 Từ ngày", value=None)
    with col2:
        den_ngay = st.date_input("📅 Đến ngày", value=None)
    with col3:
        xem_btn = st.button("📂 Xem")

    df_ls_xe = df_ls[df_ls["Biển số"] == bien_so]

    if tu_ngay and den_ngay:
        if tu_ngay > den_ngay:
            st.error("❗ Ngày bắt đầu phải nhỏ hơn hoặc bằng ngày kết thúc.")
        else:
            df_ls_xe = df_ls_xe[(df_ls_xe["Ngày"] >= pd.to_datetime(tu_ngay)) & (df_ls_xe["Ngày"] <= pd.to_datetime(den_ngay))]

    elif tu_ngay:
        df_ls_xe = df_ls_xe[df_ls_xe["Ngày"] >= pd.to_datetime(tu_ngay)]
    elif den_ngay:
        df_ls_xe = df_ls_xe[df_ls_xe["Ngày"] <= pd.to_datetime(den_ngay)]

    if df_ls_xe.empty:
        st.warning("🚫 Không có dữ liệu lịch sử bảo dưỡng.")
    else:
        st.dataframe(df_ls_xe, use_container_width=True)

        # Tổng chi phí
        if "Chi phí" in df_ls_xe.columns:
            try:
                df_ls_xe["Chi phí"] = pd.to_numeric(df_ls_xe["Chi phí"], errors="coerce")
                tong = df_ls_xe["Chi phí"].sum()
                st.markdown(f"### 💰 Tổng chi phí: **{tong:,.0f} VND**")
            except:
                st.warning("⚠️ Cột Chi phí không đúng định dạng số.")

        # Xuất Excel
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_ls_xe.to_excel(writer, index=False, sheet_name="Lich_su_bao_duong")
        st.download_button(
            label="📥 Tải xuống Excel",
            data=output.getvalue(),
            file_name=f"lich_su_{bien_so}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
