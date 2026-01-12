import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
from io import BytesIO
import xlsxwriter
from datetime import datetime, timedelta
import random
import string
from google.oauth2.service_account import Credentials
def get_remaining_hours(cap_time_str):
    try:
        cap_time = datetime.strptime(cap_time_str, "%Y-%m-%d %H:%M")
        remain = cap_time + timedelta(hours=24) - datetime.now()
        return int(remain.total_seconds() // 3600)
    except:
        return -1

def gen_access_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# ⚙️ Cấu hình Streamlit (PHẢI đặt ở đầu!)
st.set_page_config(page_title="Tra cứu lịch bảo dưỡng", layout="wide")

@st.cache_resource
def get_gsheet():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scope
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key("1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

sheet = get_gsheet()

def create_access_code(sheet, bien_so):
    ws = sheet.worksheet("CapPhep")

    new_code = gen_access_code()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    ws.append_row([new_code, bien_so, now_str])

    return new_code, now_str

# 📄 Đọc dữ liệu
df_xe = pd.DataFrame(sheet.worksheet("Xe").get_all_records())
df_ls = pd.DataFrame(sheet.worksheet("Lịch sử bảo dưỡng").get_all_records())
df_next = pd.DataFrame(sheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())
df_cap = pd.DataFrame(sheet.worksheet("CapPhep").get_all_records())

st.title("Tra cứu lịch sử bảo dưỡng xe")
# 🔐 KIỂM TRA MÃ TRUY CẬP (có hạn 24h)
if "access_info" not in st.session_state:
    st.session_state.access_info = None

if st.session_state.access_info is None:
    st.markdown("## Nhập mã truy cập")

    code = st.text_input("Mã truy cập", type="password")
    if st.button("Xác nhận"):

        # 🔑 ADMIN vào thẳng
        if code == "ADMIN":
            st.session_state.access_info = {
                "code": "ADMIN",
                "bien_so": "ALL",
                "cap_time": datetime.now()
            }
            st.experimental_rerun()

        # 🔐 Mã thường
        row = df_cap[df_cap["MaTruyCap"] == code]

        if row.empty:
            st.error("❌ Mã truy cập không tồn tại")
        else:
            cap_time = datetime.strptime(
                row.iloc[0]["ThoiDiemCap"], "%Y-%m-%d %H:%M"
            )

            if datetime.now() > cap_time + timedelta(hours=24):
                st.error("⏰ Mã truy cập đã hết hạn (24h)")
            else:
                st.session_state.access_info = {
                    "code": code,
                    "bien_so": row.iloc[0]["BienSo"],
                    "cap_time": cap_time
                }
                st.experimental_rerun()

    st.stop()

# 🔎 Xác định biển số được phép xem
if st.session_state.access_info["bien_so"] == "ALL":
    bien_so_duoc_xem = df_xe["Biển số"].dropna().unique().tolist()
else:
    bien_so_duoc_xem = [st.session_state.access_info["bien_so"]]
# 🛠️ KHU VỰC QUẢN TRỊ – CHỈ ADMIN
if st.session_state.access_info["code"] == "ADMIN":
    st.markdown("## 🛠️ Quản trị – Cấp mã truy cập")
    
    st.markdown("## Danh sách mã truy cập đang còn hiệu lực")

    ws_cap = sheet.worksheet("CapPhep")
    df_cap = pd.DataFrame(ws_cap.get_all_records())
    st.markdown("## Thu hồi mã truy cập")

    ma_thu_hoi = st.text_input("Nhập mã cần thu hồi")

    if st.button("Thu hồi ngay"):
        data = ws_cap.get_all_values()
        found = False

        for i, row in enumerate(data[1:], start=2):  # bỏ header
            if row[0] == ma_thu_hoi:
                ws_cap.delete_rows(i)
                found = True
                st.success(f"Đã thu hồi mã: {ma_thu_hoi}")
                break

        if not found:
            st.error("Không tìm thấy mã cần thu hồi")

    rows = []
    for _, r in df_cap.iterrows():
        remain_hours = get_remaining_hours(r["ThoiDiemCap"])
        if remain_hours > 0:
            rows.append({
                "Mã truy cập": r["MaTruyCap"],
                "Biển số": r["BienSo"],
                "Cấp lúc": r["ThoiDiemCap"],
                "Còn hiệu lực (giờ)": remain_hours
            })

    if rows:
        df_view = pd.DataFrame(rows).sort_values("Còn hiệu lực (giờ)")
        st.dataframe(df_view, use_container_width=True)
    else:
        st.info("Hiện không có mã truy cập nào đang còn hiệu lực.")


    bien_so_cap = st.selectbox(
        "Chọn biển số cần cấp quyền:",
        df_xe["Biển số"].dropna().unique().tolist()
    )

    if st.button("Tạo mã truy cập (hiệu lực 24h)"):
        new_code, cap_time = create_access_code(sheet, bien_so_cap)

        st.success(f"""
        Đã tạo mã truy cập thành công  
        **Mã:** `{new_code}`  
        **Biển số:** {bien_so_cap}  
        **Thời điểm cấp:** {cap_time}  
        **Hết hạn sau:** 24 giờ
        """)
        st.experimental_rerun()

# 🔒 Lọc dữ liệu theo quyền truy cập
df_xe = df_xe[df_xe["Biển số"].isin(bien_so_duoc_xem)]
df_ls = df_ls[df_ls["Biển số"].isin(bien_so_duoc_xem)]
df_next = df_next[df_next["Biển số"].isin(bien_so_duoc_xem)]

bien_so_list_sorted = sorted(bien_so_duoc_xem)

# Khởi tạo session_state nếu chưa có
if "selected_bien_so" not in st.session_state:
    st.session_state.selected_bien_so = bien_so_list_sorted[0]  # mặc định là xe đầu tiên

# Hiển thị selectbox với giá trị được lưu trong session
selected_bien_so = st.selectbox(
    "Chọn biển số xe:",
    bien_so_list_sorted,
    index=bien_so_list_sorted.index(st.session_state.selected_bien_so)
)

# Cập nhật session_state nếu người dùng chọn mới
st.session_state.selected_bien_so = selected_bien_so


# 📄 Hiển thị thông tin xe
xe_info = df_xe[df_xe["Biển số"] == selected_bien_so].iloc[0]
nam_sx_raw = xe_info.get("Năm sản xuất", "")
try:
    nam_sx = int(float(nam_sx_raw))
except:
    nam_sx = "Chưa cập nhật"
thong_tin_html = f"""
<table style="border-collapse: collapse; width: 100%;">
  <tr><td style="padding: 6px;"><b>🚗 Biển số</b></td><td style="padding: 6px;">{xe_info['Biển số']}</td></tr>
  <tr><td style="padding: 6px;"><b>🔧 Loại xe</b></td><td style="padding: 6px;">{xe_info['Loại xe']}</td></tr>
    <tr>
      <td style="padding: 6px;"><b>📅 Năm sản xuất</b></td>
      <td style="padding: 6px;">{nam_sx}</td>
    </tr>
  <tr><td style="padding: 6px;"><b>📍 Trạng thái</b></td><td style="padding: 6px;">{xe_info['Trạng thái']}</td></tr>
</table>
"""
st.markdown("### Thông tin xe")
st.markdown(thong_tin_html, unsafe_allow_html=True)

# 📅 Lịch bảo dưỡng tiếp theo
st.markdown("### Lịch bảo dưỡng tiếp theo:")
df_next_filtered = df_next[df_next["Biển số"] == selected_bien_so]
if not df_next_filtered.empty:
    st.write(f"- **Dự kiến:** {df_next_filtered.iloc[0]['Dự kiến lần tiếp theo']}")
    st.write(f"- **Gợi ý nội dung:** {df_next_filtered.iloc[0]['Gợi ý nội dung']}")
else:
    st.warning("Chưa có lịch bảo dưỡng tiếp theo.")

# 📆 Bộ lọc thời gian
st.markdown("### Lịch sử bảo dưỡng")
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
df_ls["Xem"] = "👁️ Xem"
# 📑 Giao diện bảng AgGrid
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

# Cấu hình từng cột
gb.configure_column("Biển số", width=90, cellStyle=one_line_style)
gb.configure_column("Ngày", width=90, cellStyle=one_line_style)
gb.configure_column("Chi phí hiển thị", header_name="Chi phí", width=100, cellStyle=one_line_style)
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

# Chiều cao lưới
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
st.markdown("""
<div style="
    background-color: #e8f0fe;
    padding: 10px;
    border-left: 4px solid #1a73e8;
    border-radius: 5px;
    font-weight: 500;
    color: #1a1a1a;
    margin-bottom: 10px;
">
👉 <b>Bấm vào ô <i>Nội dung</i> để xem chi tiết phía dưới.</b>
</div>
""", unsafe_allow_html=True)



# 📝 Nội dung chi tiết
selected = grid_response.get("selected_rows", [])
if selected and "Nội dung" in selected[0]:
    st.markdown("#### Nội dung chi tiết:")
    st.info(selected[0]["Nội dung"])

# 💰 Tổng chi phí
tong_chi_phi = df_ls["Chi phí"].sum()
st.markdown(f"#### Tổng chi phí: `{tong_chi_phi:,.0f} VND`".replace(",", "."))

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
