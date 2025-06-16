import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from st_aggrid import AgGrid, GridOptionsBuilder
from io import BytesIO
from datetime import datetime

# Cấu hình scope và xác thực
scope = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
credentials = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=scope
)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key("1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

# Lấy dữ liệu từ các sheet
df_xe = pd.DataFrame(sheet.worksheet("Xe").get_all_records())
df_lichsu = pd.DataFrame(sheet.worksheet("Lịch sử bảo dưỡng").get_all_records())
df_sau = pd.DataFrame(sheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())

# Format ngày
df_lichsu["Ngày"] = pd.to_datetime(df_lichsu["Ngày"], errors="coerce")
df_sau["Dự kiến lần tiếp theo"] = pd.to_datetime(df_sau["Dự kiến lần tiếp theo"], errors="coerce")

# Giao diện
st.title("🛠️ Tra cứu lịch sử bảo dưỡng xe")

bien_so_list = df_xe["Biển số"].unique()
selected_bs = st.selectbox("Chọn biển số xe", bien_so_list)

# Thông tin xe
xe_row = df_xe[df_xe["Biển số"] == selected_bs].iloc[0]
col1, col2, col3, col4 = st.columns(4)
col1.metric("🚗 Biển số", xe_row["Biển số"])
col2.metric("📋 Loại xe", xe_row["Loại xe"])
col3.metric("🛠️ Năm sản xuất", f"{int(xe_row['Năm sản xuất'])}")
col4.metric("📌 Trạng thái", xe_row["Trạng thái"])

# Thông tin lịch bảo dưỡng tiếp theo
st.markdown("### 📅 Lịch bảo dưỡng tiếp theo:")
row_next = df_sau[df_sau["Biển số"] == selected_bs]
if not row_next.empty:
    r = row_next.iloc[0]
    st.info(f"**Ngày dự kiến**: {r['Dự kiến lần tiếp theo'].strftime('%d/%m/%Y')} | **Gợi ý nội dung**: {r['Gợi ý nội dung']}")
else:
    st.warning("Chưa có lịch bảo dưỡng tiếp theo.")

# Lọc thời gian
st.markdown("### 📂 Lịch sử bảo dưỡng")
df_bs = df_lichsu[df_lichsu["Biển số"] == selected_bs].copy()
col1, col2, col3 = st.columns([1, 1, 1])
from_date = col1.date_input("Từ ngày", value=None, format="DD/MM/YYYY")
to_date = col2.date_input("Đến ngày", value=None, format="DD/MM/YYYY")
col3.write("")
if col3.button("Xem"):
    if from_date and to_date and from_date > to_date:
        st.error("⚠️ 'Từ ngày' phải nhỏ hơn hoặc bằng 'Đến ngày'")
    else:
        if from_date:
            df_bs = df_bs[df_bs["Ngày"] >= pd.to_datetime(from_date)]
        if to_date:
            df_bs = df_bs[df_bs["Ngày"] <= pd.to_datetime(to_date)]

# Định dạng dữ liệu
df_bs["Ngày"] = df_bs["Ngày"].dt.strftime("%d/%m/%Y")
df_bs["Chi phí"] = pd.to_numeric(df_bs["Chi phí"], errors="coerce").fillna(0)

# Hiển thị bảng bằng AgGrid
gb = GridOptionsBuilder.from_dataframe(df_bs)
gb.configure_default_column(wrapText=True, autoHeight=True)
gb.configure_column("Nội dung", cellRenderer='''function(params) {
    if (params.value.length > 50) {
        return `<span title="${params.value}">${params.value.substring(0, 50)}...</span>`;
    } else {
        return params.value;
    }
}''')
gridOptions = gb.build()

st.markdown("#### 📜 Chi tiết lịch sử bảo dưỡng")
AgGrid(df_bs, gridOptions=gridOptions, fit_columns_on_grid_load=True, height=min(400, 40 * len(df_bs) + 100), theme="alpine")

# Tổng chi phí
tong_tien = df_bs["Chi phí"].sum()
st.success(f"**💰 Tổng chi phí:** {tong_tien:,.0f} VND")

# Xuất Excel
buffer = BytesIO()
with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
    df_bs.to_excel(writer, sheet_name="LichSu", index=False)
    writer.close()
btn = st.download_button("📤 Xuất Excel", data=buffer.getvalue(), file_name=f"{selected_bs}_lich_su.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
