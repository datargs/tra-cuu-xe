import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
from st_aggrid import GridOptionsBuilder, AgGrid
from io import BytesIO
from datetime import datetime

# Load secrets từ phần cấu hình
creds = service_account.Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)

gc = gspread.authorize(creds)
spreadsheet = gc.open_by_url("https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

# Load các sheet
df_xe = pd.DataFrame(spreadsheet.worksheet("Xe").get_all_records())
df_ls = pd.DataFrame(spreadsheet.worksheet("Lịch sử bảo dưỡng").get_all_records())
df_next = pd.DataFrame(spreadsheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())

# Chuyển cột ngày sang định dạng datetime
df_ls["Ngày"] = pd.to_datetime(df_ls["Ngày"], errors="coerce")

# Giao diện chọn biển số
st.title("🔧 Tra cứu lịch sử & lịch bảo dưỡng xe")
selected_plate = st.selectbox("Chọn biển số xe", df_xe["Biển số"].unique(), key="bienso", index=0)

# Hiển thị thông tin xe
df_info = df_xe[df_xe["Biển số"] == selected_plate]
st.subheader("📄 Thông tin xe")
st.write(df_info.iloc[0])

# Lịch bảo dưỡng tiếp theo
df_next_match = df_next[df_next["Biển số"] == selected_plate]
st.subheader("🛠 Lịch bảo dưỡng tiếp theo")
if not df_next_match.empty:
    for col in df_next_match.columns:
        st.markdown(f"**{col}:** {df_next_match.iloc[0][col]}")
else:
    st.warning("Chưa có lịch bảo dưỡng tiếp theo.")

# Bộ lọc thời gian
st.subheader("🕒 Lịch sử bảo dưỡng")
col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    from_date = st.date_input("📆 Từ ngày", value=None, key="from_date")
    if from_date:
        st.markdown(f"`Từ ngày: {from_date.strftime('%d/%m/%Y')}`")
with col2:
    to_date = st.date_input("📆 Đến ngày", value=None, key="to_date")
    if to_date:
        st.markdown(f"`Đến ngày: {to_date.strftime('%d/%m/%Y')}`")
with col3:
    xem = st.button("📂 Xem")

# Lọc lịch sử
df_ls_filtered = df_ls[df_ls["Biển số"] == selected_plate]

if xem:
    if from_date and to_date:
        if from_date > to_date:
            st.error("❌ Từ ngày phải nhỏ hơn hoặc bằng đến ngày.")
        else:
            df_ls_filtered = df_ls_filtered[(df_ls_filtered["Ngày"] >= pd.to_datetime(from_date)) & (df_ls_filtered["Ngày"] <= pd.to_datetime(to_date))]
    elif from_date:
        df_ls_filtered = df_ls_filtered[df_ls_filtered["Ngày"] >= pd.to_datetime(from_date)]
    elif to_date:
        df_ls_filtered = df_ls_filtered[df_ls_filtered["Ngày"] <= pd.to_datetime(to_date)]

# Định dạng lại ngày
df_ls_filtered["Ngày"] = df_ls_filtered["Ngày"].dt.strftime("%d/%m/%Y")

# Tính tổng chi phí
df_ls_filtered["Chi phí"] = pd.to_numeric(df_ls_filtered["Chi phí"], errors="coerce")
tong_tien = df_ls_filtered["Chi phí"].sum()

# Hiển thị bảng
gb = GridOptionsBuilder.from_dataframe(df_ls_filtered)
gb.configure_default_column(wrapText=True, autoHeight=True)
gb.configure_column("Nội dung", wrapText=True, autoHeight=True)
gb.configure_grid_options(domLayout='normal')
grid_options = gb.build()

AgGrid(df_ls_filtered, gridOptions=grid_options, height=400, fit_columns_on_grid_load=True)

# Tổng chi phí
st.markdown(f"**💰 Tổng chi phí:** {tong_tien:,.0f} VND")

# Xuất Excel
def convert_df_to_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='LichSuBaoDuong', index=False)
    processed_data = output.getvalue()
    return processed_data

st.download_button(
    label="📥 Xuất Excel lịch sử bảo dưỡng",
    data=convert_df_to_excel(df_ls_filtered),
    file_name=f"lich_su_bao_duong_{selected_plate}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
