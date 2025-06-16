import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
from io import BytesIO
import xlsxwriter
from datetime import datetime

# ⚙️ Cấu hình Streamlit (PHẢI đặt ở đầu!)
st.set_page_config(page_title="Tra cứu lịch bảo dưỡng", layout="wide")

# 🔐 Kết nối Google Sheets
scope = ["https://www.googleapis.com/auth/spreadsheets"]
creds = service_account.Credentials.from_service_account_info(
    st.secrets["gcp_service_account"], scopes=scope
)
gc = gspread.authorize(creds)
sheet = gc.open_by_key("1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

# 📄 Đọc dữ liệu
df_xe = pd.DataFrame(sheet.worksheet("Xe").get_all_records())
df_ls = pd.DataFrame(sheet.worksheet("Lịch sử bảo dưỡng").get_all_records())
df_next = pd.DataFrame(sheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())

st.title("🔧 Tra cứu lịch sử bảo dưỡng xe")

# 📌 Chọn biển số
bien_so_list = df_xe["Biển số"].dropna().unique().tolist()
selected_bien_so = st.selectbox("📌 Chọn biển số xe:", sorted(bien_so_list))

# 📄 Hiển thị thông tin xe
xe_info = df_xe[df_xe["Biển số"] == selected_bien_so].iloc[0]
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

# 📅 Lịch bảo dưỡng tiếp theo
st.markdown("### 📅 Lịch bảo dưỡng tiếp theo:")
df_next_filtered = df_next[df_next["Biển số"] == selected_bien_so]
if not df_next_filtered.empty:
    st.write(f"- **Dự kiến:** {df_next_filtered.iloc[0]['Dự kiến lần tiếp theo']}")
    st.write(f"- **Gợi ý nội dung:** {df_next_filtered.iloc[0]['Gợi ý nội dung']}")
else:
    st.warning("Chưa có lịch bảo dưỡng tiếp theo.")

# 📆 Bộ lọc thời gian
st.markdown("### 📋 Lịch sử bảo dưỡng")
col_tu, col_den, col_xem = st.columns([2, 2, 1])
tu_ngay = col_tu.date_input("Từ ngày (DD/MM/YYYY)", format="DD/MM/YYYY", value=None)
den_ngay = col_den.date_input("Đến ngày (DD/MM/YYYY)", format="DD/MM/YYYY", value=None)
filter_btn = col_xem.button("🔍 Xem")

# 📊 Xử lý lịch sử bảo dưỡng
df_ls = df_ls[df_ls["Biển số"] == selected_bien_so]
df_ls["Ngày"] = pd.to_datetime(df_ls["Ngày"], errors="coerce")
df_ls = df_ls.dropna(subset=["Ngày"])

if filter_btn and tu_ngay and den_ngay:
    if tu_ngay > den_ngay:
        st.error("❗️Từ ngày phải nhỏ hơn hoặc bằng Đến ngày.")
    else:
        df_ls = df_ls[(df_ls["Ngày"].dt.date >= tu_ngay) & (df_ls["Ngày"].dt.date <= den_ngay)]

df_ls["Ngày"] = df_ls["Ngày"].dt.strftime("%d/%m/%Y")
df_ls["Chi phí"] = pd.to_numeric(df_ls["Chi phí"], errors="coerce").fillna(0)
df_ls["Chi phí hiển thị"] = df_ls["Chi phí"].apply(lambda x: f"{x:,.0f}".replace(",", "."))

# 📑 Giao diện bảng AgGrid
gb = GridOptionsBuilder.from_dataframe(df_ls[["Biển số", "Ngày", "Nội dung", "Chi phí hiển thị"]])

one_line_style = JsCode("""
    function(params) {
        return {
            'white-space': 'nowrap',
            'overflow': 'hidden',
            'text-overflow': 'ellipsis'
        }
    }
""")

# Cột ngắn
gb.configure_column("Biển số", width=90, cellStyle=one_line_style)
gb.configure_column("Ngày", width=90, cellStyle=one_line_style)
gb.configure_column("Chi phí hiển thị", header_name="Chi phí", width=100, cellStyle=one_line_style)

# Cột nội dung dài
gb.configure_column("Nội dung", width=120, cellStyle=JsCode("""
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
row_height = 38
padding = 60
grid_height = len(df_ls) * row_height + padding
grid_height = max(150, min(600, grid_height))

grid_response = AgGrid(
    df_ls[["Biển số", "Ngày", "Nội dung", "Chi phí hiển thị"]],
    gridOptions=grid_options,
    height=grid_height,
    width="100%",
    fit_columns_on_grid_load=False,
    update_mode=GridUpdateMode.SELECTION_CHANGED,
    allow_unsafe_jscode=True,
)


# 📝 Nội dung chi tiết
selected = grid_response.get("selected_rows", [])
if selected and "Nội dung" in selected[0] and selected[0]["Nội dung"].strip():
    st.markdown("#### 📝 Nội dung chi tiết:")
    st.markdown(f"""
    <div style="padding: 10px; background-color: #f1f3f6; border-radius: 5px; border: 1px solid #ccc;">
        {selected[0]["Nội dung"]}
    </div>
    """, unsafe_allow_html=True)

# 💰 Tổng chi phí
tong_chi_phi = df_ls["Chi phí"].sum()
st.markdown(f"#### 💵 Tổng chi phí: `{tong_chi_phi:,.0f} VND`".replace(",", "."))

# 📥 Xuất Excel
output = BytesIO()
with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
    df_ls[["Biển số", "Ngày", "Nội dung", "Chi phí"]].to_excel(writer, index=False, sheet_name="LichSuBaoDuong")

st.download_button(
    label="📥 Xuất Excel",
    data=output.getvalue(),
    file_name=f"lich_su_bao_duong_{selected_bien_so}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
