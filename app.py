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

# Lấy thông tin xe theo biển số
xe_info = df_xe[df_xe["Biển số"] == selected_bien_so].iloc[0]

# Tạo HTML bảng gọn gàng
thong_tin_html = f"""
<table style="border-collapse: collapse; width: 100%;">
  <tr><td style="padding: 6px;"><b>🚗 Biển số</b></td><td style="padding: 6px;">{xe_info['Biển số']}</td></tr>
  <tr><td style="padding: 6px;"><b>🔧 Loại xe</b></td><td style="padding: 6px;">{xe_info['Loại xe']}</td></tr>
  <tr><td style="padding: 6px;"><b>📅 Năm sản xuất</b></td><td style="padding: 6px;">{int(xe_info['Năm sản xuất'])}</td></tr>
  <tr><td style="padding: 6px;"><b>📍 Trạng thái</b></td><td style="padding: 6px;">{xe_info['Trạng thái']}</td></tr>
</table>
"""

st.markdown("### 📄 Thông tin xe")
st.markdown(thong_tin_html, unsafe_allow_html=True)


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
df_ls["Chi phí"] = df_ls["Chi phí"].apply(lambda x: f"{x:,.0f}".replace(",", "."))


from st_aggrid import JsCode

gb = GridOptionsBuilder.from_dataframe(df_ls)

# Các cột hiển thị một dòng
one_line_style = JsCode("""
    function(params) {
        return {
            'white-space': 'nowrap',
            'overflow': 'hidden',
            'text-overflow': 'ellipsis'
        }
    }
""")

# Cột biển số, ngày, chi phí: không wrap
gb.configure_column("Biển số", wrapText=False, autoHeight=False, width=90, cellStyle=one_line_style)
gb.configure_column("Ngày", wrapText=False, autoHeight=False, width=90, cellStyle=one_line_style)
gb.configure_column("Nội dung", wrapText=False, autoHeight=False, width=120, cellStyle=one_line_style)
gb.configure_column("Chi phí", wrapText=False, autoHeight=False, width=90, cellStyle=one_line_style)

# Cột nội dung: 1 dòng, dấu ba chấm
gb.configure_column("Nội dung", wrapText=False, autoHeight=False, cellStyle=JsCode("""
    function(params) {
        return {
            'white-space': 'nowrap',
            'overflow': 'hidden',
            'text-overflow': 'ellipsis',
            'maxWidth': '250px'
        };
    }
"""))

gb.configure_grid_options(domLayout='normal', suppressRowClickSelection=False)
grid_options = gb.build()


st.markdown("### 📑 Chi tiết lịch sử bảo dưỡng")
# Tính chiều cao động chính xác hơn
row_height = 38  # mỗi dòng khoảng 38px
padding = 60     # chừa thêm khoảng đệm
grid_height = len(df_ls) * row_height + padding
grid_height = max(150, min(600, grid_height))  # giới hạn chiều cao

grid_response = AgGrid(
    df_ls,
    gridOptions=grid_options,
    height=grid_height,
    width="100%",
    fit_columns_on_grid_load=False,
    update_mode=GridUpdateMode.SELECTION_CHANGED,
    allow_unsafe_jscode=True
)


# Hiển thị nội dung khi chọn dòng
selected = grid_response["selected_rows"]
if selected:
    st.markdown("#### 📝 Nội dung chi tiết:")
    st.info(selected[0]["Nội dung"])

# Tổng chi phí
tong_chi_phi = df_ls["Chi phí"].sum()
st.markdown(f"#### 💵 Tổng chi phí: `{tong_chi_phi:,.0f} VND`")

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
