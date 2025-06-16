import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
from io import BytesIO
import xlsxwriter
from datetime import datetime

# Kết nối Google Sheets
scope = ["https://www.googleapis.com/auth/spreadsheets"]
creds = service_account.Credentials.from_service_account_info(
    st.secrets["gcp_service_account"], scopes=scope
)
gc = gspread.authorize(creds)
sheet = gc.open_by_key("1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

# Đọc dữ liệu
df_xe = pd.DataFrame(sheet.worksheet("Xe").get_all_records())
df_ls = pd.DataFrame(sheet.worksheet("Lịch sử bảo dưỡng").get_all_records())
df_next = pd.DataFrame(sheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())

# Cấu hình Streamlit
st.set_page_config(page_title="Tra cứu lịch bảo dưỡng", layout="wide")
st.title("🔧 Tra cứu lịch sử bảo dưỡng xe")

# Chọn biển số
bien_so_list = df_xe["Biển số"].dropna().unique().tolist()
selected_bien_so = st.selectbox("📌 Chọn biển số xe:", sorted(bien_so_list))

# Tạo bảng thông tin xe không index, không header
xe_info = df_xe[df_xe["Biển số"] == selected_bien_so].iloc[0]
data = [
    ["🚗 Biển số", xe_info["Biển số"]],
    ["🔧 Loại xe", xe_info["Loại xe"]],
    ["📅 Năm sản xuất", f"{int(xe_info['Năm sản xuất'])}"],
    ["📍 Trạng thái", xe_info["Trạng thái"]],
]

# Hiển thị bảng nhỏ gọn, không index, không tiêu đề
st.markdown("### 📄 Thông tin xe")
st.table(pd.DataFrame(data).style.hide(axis="columns").hide(axis="index"))

# Hiển thị lịch bảo dưỡng tiếp theo
st.markdown("### 📅 Lịch bảo dưỡng tiếp theo:")
df_next_filtered = df_next[df_next["Biển số"] == selected_bien_so]
if not df_next_filtered.empty:
    st.write(f"- **Dự kiến:** {df_next_filtered.iloc[0]['Dự kiến lần tiếp theo']}")
    st.write(f"- **Gợi ý nội dung:** {df_next_filtered.iloc[0]['Gợi ý nội dung']}")
else:
    st.warning("Chưa có lịch bảo dưỡng tiếp theo.")

# Bộ lọc thời gian
st.markdown("### 📋 Lịch sử bảo dưỡng")
col_tu, col_den, col_xem = st.columns([2, 2, 1])
tu_ngay = col_tu.date_input("Từ ngày (DD/MM/YYYY)", format="DD/MM/YYYY", value=None)
den_ngay = col_den.date_input("Đến ngày (DD/MM/YYYY)", format="DD/MM/YYYY", value=None)
filter_btn = col_xem.button("🔍 Xem")

# Xử lý dữ liệu bảo dưỡng
df_ls = df_ls[df_ls["Biển số"] == selected_bien_so]
df_ls["Ngày"] = pd.to_datetime(df_ls["Ngày"], errors="coerce")
df_ls = df_ls.dropna(subset=["Ngày"])

# Lọc thời gian
if filter_btn and tu_ngay and den_ngay:
    if tu_ngay > den_ngay:
        st.error("❗️Từ ngày phải nhỏ hơn hoặc bằng Đến ngày.")
    else:
        df_ls = df_ls[(df_ls["Ngày"].dt.date >= tu_ngay) & (df_ls["Ngày"].dt.date <= den_ngay)]

# Format cột
df_ls["Ngày"] = df_ls["Ngày"].dt.strftime("%d/%m/%Y")
df_ls["Chi phí"] = pd.to_numeric(df_ls["Chi phí"], errors="coerce").fillna(0)

# Giao diện AgGrid
gb = GridOptionsBuilder.from_dataframe(df_ls)
gb.configure_column("Nội dung", wrapText=True, autoHeight=True, cellRenderer="""
    function(params) {
        let val = params.value;
        if (!val) return '';
        return val.length > 50 ? val.substring(0, 50) + '...' : val;
    }
""")
gb.configure_grid_options(domLayout='normal', suppressRowClickSelection=False)
grid_options = gb.build()

st.markdown("### 📑 Chi tiết lịch sử bảo dưỡng")
grid_response = AgGrid(
    df_ls,
    gridOptions=grid_options,
    height=min(500, 40 + 35 * len(df_ls)),
    width="100%",
    fit_columns_on_grid_load=True,
    update_mode=GridUpdateMode.SELECTION_CHANGED,
    allow_unsafe_jscode=True,
)

# Hiển thị nội dung khi chọn dòng
selected = grid_response["selected_rows"]
if selected:
    st.markdown("#### 📝 Nội dung chi tiết:")
    st.info(selected[0]["Nội dung"])

# Tổng chi phí
tong_chi_phi = df_ls["Chi phí"].sum()
st.markdown(f"#### 💵 Tổng chi phí bảo dưỡng: `{tong_chi_phi:,.0f} VND`")

# Xuất Excel
output = BytesIO()
with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
    df_ls.to_excel(writer, index=False, sheet_name="LichSuBaoDuong")
st.download_button(
    label="📥 Xuất Excel",
    data=output.getvalue(),
    file_name=f"lich_su_bao_duong_{selected_bien_so}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
