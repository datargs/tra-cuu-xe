import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
from datetime import datetime
from io import BytesIO

# Kết nối Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
client = gspread.authorize(creds)
spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

# Lấy dữ liệu
df_ls = pd.DataFrame(spreadsheet.worksheet("Lịch sử bảo dưỡng").get_all_records())
df_tt = pd.DataFrame(spreadsheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())

# Định dạng ngày
df_ls["Ngày"] = pd.to_datetime(df_ls["Ngày"], dayfirst=True, errors='coerce')
df_tt["Dự kiến lần tiếp theo"] = pd.to_datetime(df_tt["Dự kiến lần tiếp theo"], dayfirst=True, errors='coerce')

# Giao diện
st.set_page_config(page_title="Tra cứu lịch sử bảo dưỡng", layout="wide")
st.title("🔧 Tra cứu lịch sử & lịch bảo dưỡng xe")

# Chọn biển số
unique_plates = df_ls["Biển số"].dropna().unique().tolist()
selected_plate = st.selectbox("🔍 Chọn biển số xe", sorted(unique_plates))

if selected_plate:
    # Hiển thị lịch bảo dưỡng tiếp theo
    st.subheader("📅 Lịch bảo dưỡng tiếp theo:")
    next_maint = df_tt[df_tt["Biển số"] == selected_plate]
    if not next_maint.empty:
        st.write("**Dự kiến:**", next_maint.iloc[0]["Dự kiến lần tiếp theo"].strftime("%d/%m/%Y"))
        st.write("**Gợi ý nội dung:**", next_maint.iloc[0]["Gợi ý nội dung"])
    else:
        st.info("Chưa có lịch bảo dưỡng tiếp theo.")

    # Lọc theo ngày
    st.subheader("📘 Lịch sử bảo dưỡng:")
    col1, col2, col3 = st.columns([1.5, 1.5, 1])
    with col1:
        from_date = st.date_input("Từ ngày", value=None)
    with col2:
        to_date = st.date_input("Đến ngày", value=None)
    with col3:
        if st.button("Xem"):
            if from_date and to_date and from_date > to_date:
                st.error("⚠️ Từ ngày phải nhỏ hơn hoặc bằng đến ngày")
            else:
                st.session_state["filter"] = True
                st.session_state["from_date"] = from_date
                st.session_state["to_date"] = to_date

    df_filtered = df_ls[df_ls["Biển số"] == selected_plate].copy()

    if st.session_state.get("filter", False):
        f = st.session_state.get("from_date")
        t = st.session_state.get("to_date")
        if f:
            df_filtered = df_filtered[df_filtered["Ngày"] >= pd.to_datetime(f)]
        if t:
            df_filtered = df_filtered[df_filtered["Ngày"] <= pd.to_datetime(t)]

    df_filtered["Ngày hiển thị"] = df_filtered["Ngày"].dt.strftime("%d/%m/%Y")
    df_display = df_filtered[["Ngày hiển thị", "Nội dung", "Chi phí", "Ghi chú"]].rename(
        columns={"Ngày hiển thị": "Ngày"}
    )

    # Tính tổng chi phí
    df_filtered["Chi phí"] = pd.to_numeric(df_filtered["Chi phí"], errors="coerce")
    total_cost = df_filtered["Chi phí"].sum()

    # AgGrid hiển thị bảng
    gb = GridOptionsBuilder.from_dataframe(df_display)
    gb.configure_column("Nội dung", wrapText=False, autoHeight=False, tooltipField="Nội dung")
    gb.configure_selection('single')
    gb.configure_grid_options(domLayout='normal')
    grid_response = AgGrid(
        df_display,
        gridOptions=gb.build(),
        height=400,
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        fit_columns_on_grid_load=True,
    )

    if grid_response['selected_rows']:
        st.markdown("### 📄 Nội dung chi tiết:")
        st.info(grid_response['selected_rows'][0]["Nội dung"])

    st.markdown(f"### 💰 Tổng chi phí: **{total_cost:,.0f} VND**")

    # Xuất Excel
    def convert_df_to_excel(df):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='LichSuBaoDuong')
        return output.getvalue()

    excel_data = convert_df_to_excel(df_display)
    st.download_button("⬇️ Xuất Excel", data=excel_data, file_name="lich_su_bao_duong.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
