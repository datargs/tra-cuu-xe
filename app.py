import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from io import BytesIO
from datetime import datetime
from st_aggrid import AgGrid, GridOptionsBuilder
from st_aggrid.shared import GridUpdateMode

# Kết nối Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
client = gspread.authorize(creds)

sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

df_xe = pd.DataFrame(sheet.worksheet("Xe").get_all_records())
df_ls = pd.DataFrame(sheet.worksheet("Lịch sử bảo dưỡng").get_all_records())
df_next = pd.DataFrame(sheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())

st.set_page_config(layout="wide")
st.title("🚗 Tra cứu bảo dưỡng xe")

# Dropdown chọn biển số
bien_so_list = df_xe["Biển số"].unique().tolist()
selected_bien_so = st.selectbox("Chọn biển số", bien_so_list)

if selected_bien_so:
    xe_info = df_xe[df_xe["Biển số"] == selected_bien_so].iloc[0]
    st.subheader("🔍 Thông tin xe")
    st.write(xe_info)

    st.subheader("📅 Lịch bảo dưỡng tiếp theo")
    df_next_match = df_next[df_next["Biển số"] == selected_bien_so]
    if not df_next_match.empty:
        st.write(df_next_match.iloc[0])
    else:
        st.info("Chưa có lịch bảo dưỡng tiếp theo")

    st.subheader("🛠 Lịch sử bảo dưỡng")

    # Bộ lọc thời gian
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        from_date = st.date_input("📆 Từ ngày", value=None)
    with col2:
        to_date = st.date_input("📆 Đến ngày", value=None)
    with col3:
        xem = st.button("Xem")

    df_ls_filtered = df_ls[df_ls["Biển số"] == selected_bien_so].copy()

    df_ls_filtered["Ngày"] = pd.to_datetime(df_ls_filtered["Ngày"], errors="coerce")

    if xem and from_date and to_date:
        if from_date > to_date:
            st.error("❌ Từ ngày phải nhỏ hơn hoặc bằng Đến ngày.")
        else:
            df_ls_filtered = df_ls_filtered[
                (df_ls_filtered["Ngày"] >= pd.to_datetime(from_date)) &
                (df_ls_filtered["Ngày"] <= pd.to_datetime(to_date))
            ]

    # Định dạng lại ngày
    df_ls_filtered["Ngày"] = df_ls_filtered["Ngày"].dt.strftime("%d/%m/%Y")

    # Hiển thị AgGrid
    if df_ls_filtered.empty:
        st.info("Không có dữ liệu lịch sử bảo dưỡng.")
    else:
        st.markdown("### 📋 Danh sách lịch sử bảo dưỡng")

        try:
            df_ls_filtered["Chi phí"] = pd.to_numeric(df_ls_filtered["Chi phí"], errors="coerce")
        except:
            st.warning("Không thể chuyển cột Chi phí về dạng số.")

        gb = GridOptionsBuilder.from_dataframe(df_ls_filtered)
        gb.configure_default_column(wrapText=True, autoHeight=True, resizable=True, filter=True)
        gb.configure_grid_options(domLayout='normal')
        gb.configure_column("Nội dung", autoHeight=True, wrapText=True)
        gridOptions = gb.build()

        AgGrid(
            df_ls_filtered,
            gridOptions=gridOptions,
            update_mode=GridUpdateMode.NO_UPDATE,
            fit_columns_on_grid_load=True,
            height=400,
            allow_unsafe_jscode=True,
            theme="alpine"
        )

        # Tổng chi phí
        if "Chi phí" in df_ls_filtered:
            tong = df_ls_filtered["Chi phí"].sum()
            st.markdown(f"### 💰 Tổng chi phí: **{tong:,.0f} VND**")

        # Xuất Excel
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_ls_filtered.to_excel(writer, index=False, sheet_name="LichSuBaoDuong")
            writer.save()
        st.download_button(
            label="📥 Xuất Excel",
            data=output.getvalue(),
            file_name=f"lich_su_{selected_bien_so}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
