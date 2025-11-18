import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
from io import BytesIO
from datetime import datetime

# ⚙️ Streamlit config
st.set_page_config(page_title="Tra cứu lịch bảo dưỡng", layout="wide")

# 🔐 Kết nối Google Sheets (KHÔNG CACHE)
scope = ["https://www.googleapis.com/auth/spreadsheets"]
creds = service_account.Credentials.from_service_account_info(
    st.secrets["gcp_service_account"], scopes=scope
)
gc = gspread.authorize(creds)
sheet = gc.open_by_key("1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")


# ======================================================
#  ⭐ HÀM ĐỌC GOOGLE SHEET MỚI 100% – KHÔNG BỊ CACHE
# ======================================================
def load_sheet(name):
    ws = sheet.worksheet(name)
    values = ws.get_all_values()
    if len(values) == 0:
        return pd.DataFrame()
    header = values[0]
    rows = values[1:]
    return pd.DataFrame(rows, columns=header)


# 📄 Đọc dữ liệu (luôn lấy bản mới)
df_xe = load_sheet("Xe")
df_ls = load_sheet("Lịch sử bảo dưỡng")
df_next = load_sheet("Lịch bảo dưỡng tiếp theo")


# ======================================================
#  GIAO DIỆN
# ======================================================
st.title("🔧 Tra cứu lịch sử bảo dưỡng xe")

# Tạo danh sách biển số
bien_so_list = df_xe["Biển số"].dropna().unique().tolist()
bien_so_list_sorted = sorted(bien_so_list)

if "selected_bien_so" not in st.session_state:
    st.session_state.selected_bien_so = bien_so_list_sorted[0]

selected_bien_so = st.selectbox(
    "📌 Chọn biển số xe:",
    bien_so_list_sorted,
    index=bien_so_list_sorted.index(st.session_state.selected_bien_so)
)

st.session_state.selected_bien_so = selected_bien_so

# 📄 Hiển thị thông tin xe
xe_info = df_xe[df_xe["Biển số"] == selected_bien_so].iloc[0]

# Xử lý năm sản xuất
try:
    nam_sx_dt = pd.to_datetime(xe_info["Năm sản xuất"], errors="coerce")
    nam_sx = nam_sx_dt.strftime("%d/%m/%Y") if pd.notnull(nam_sx_dt) else xe_info["Năm sản xuất"]
except:
    nam_sx = xe_info["Năm sản xuất"]

thong_tin_html = f"""
<table style="border-collapse: collapse; width: 100%;">
  <tr><td style="padding: 6px;"><b>🚗 Biển số</b></td><td style="padding: 6px;">{xe_info['Biển số']}</td></tr>
  <tr><td style="padding: 6px;"><b>🔧 Loại xe</b></td><td style="padding: 6px;">{xe_info['Loại xe']}</td></tr>
  <tr><td style="padding: 6px;"><b>📅 Năm sản xuất</b></td><td style="padding: 6px;">{nam_sx}</td></tr>
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

# =========================================
#  LỊCH SỬ BẢO DƯỠNG
# =========================================
st.markdown("### 📋 Lịch sử bảo dưỡng")

col_tu, col_den, col_xem = st.columns([2, 2, 1])
tu_ngay = col_tu.date_input("Từ ngày (DD/MM/YYYY)", format="DD/MM/YYYY", value=None)
den_ngay = col_den.date_input("Đến ngày (DD/MM/YYYY)", format="DD/MM/YYYY", value=None)
filter_btn = col_xem.button("🔍 Xem")

df_ls = df_ls[df_ls["Biển số"] == selected_bien_so]
df_ls["Ngày"] = pd.to_datetime(df_ls["Ngày"], errors="coerce")
df_ls = df_ls.dropna(subset=["Ngày"])

if filter_btn and tu_ngay and den_ngay:
    if tu_ngay > den_ngay:
        st.error("❗ Từ ngày phải nhỏ hơn hoặc bằng Đến ngày.")
    else:
        df_ls = df_ls[(df_ls["Ngày"].dt.date >= tu_ngay) & (df_ls["Ngày"].dt.date <= den_ngay)]

df_ls["Ngày"] = df_ls["Ngày"].dt.strftime("%d/%m/%Y")
df_ls["Chi phí"] = pd.to_numeric(df_ls["Chi phí"], errors="coerce").fillna(0)
df_ls["Chi phí hiển thị"] = df_ls["Chi phí"].apply(lambda x: f"{x:,.0f}".replace(",", "."))

# =========================================
#  AG-GRID
# =========================================
gb = GridOptionsBuilder.from_dataframe(df_ls[["Biển số", "Ngày", "Nội dung", "Chi phí hiển thị"]])
gb.configure_selection("single", use_checkbox=False)

one_line_style = JsCode("""
    function(params) {
        return {
            'white-space': 'nowrap',
            'overflow': 'hidden',
            'text-overflow': 'ellipsis'
        }
    }
""")

gb.configure_column("Biển số", width=90, cellStyle=one_line_style)
gb.configure_column("Ngày", width=90, cellStyle=one_line_style)
gb.configure_column("Chi phí hiển thị", header_name="Chi phí", width=100, cellStyle=one_line_style)
gb.configure_column("Nội dung", width=120, cellStyle=one_line_style)

grid_options = gb.build()

row_height = 38
padding = 60
grid_height = min(600, max(150, len(df_ls) * row_height + padding))

grid_response = AgGrid(
    df_ls[["Biển số", "Ngày", "Nội dung", "Chi phí hiển thị"]],
    gridOptions=grid_options,
    height=grid_height,
    width="100%",
    update_mode=GridUpdateMode.SELECTION_CHANGED,
    allow_unsafe_jscode=True,
)

st.markdown("""
<div style="
    background-color: #e8f0fe;
    padding: 10px;
    border-left: 4px solid #1a73e8;
    border-radius: 5px;
    font-weight: 500;
    color: #1a1a1a;
    margin-bottom: 10px;">
👉 <b>Bấm vào ô <i>Nội dung</i> để xem chi tiết phía dưới.</b>
</div>
""", unsafe_allow_html=True)

# 📝 Chi tiết
selected = grid_response.get("selected_rows", [])
if selected:
    st.markdown("#### 📝 Nội dung chi tiết:")
    st.info(selected[0]["Nội dung"])

# 💰 Tổng chi phí
tong_chi_phi = df_ls["Chi phí"].sum()
st.markdown(f"#### 💵 Tổng chi phí: `{tong_chi_phi:,.0f} VND`".replace(",", "."))

# 📥 Xuất Excel
output = BytesIO()
df_export = df_ls[["Biển số", "Ngày", "Nội dung", "Chi phí"]]
df_export.to_excel(output, index=False, sheet_name="LichSuBaoDuong")

st.download_button(
    label="📥 Xuất Excel",
    data=output.getvalue(),
    file_name=f"lich_su_bao_duong_{selected_bien_so}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
