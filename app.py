import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
import random
import string
from io import BytesIO

# =============================
# CẤU HÌNH TRANG
# =============================
st.set_page_config(
    page_title="Tra cứu xe",
    layout="wide"
)

# =============================
# CSS GIAO DIỆN
# =============================
st.markdown("""
<style>
body {
    background-color: #f6f8fb;
}
.block-container {
    padding-top: 1.5rem;
}
.card {
    background-color: #ffffff;
    padding: 16px;
    border-radius: 8px;
    border: 1px solid #e3e6ef;
    margin-bottom: 16px;
}
.card-title {
    font-weight: 600;
    font-size: 16px;
    margin-bottom: 8px;
}
.admin-box {
    background-color: #eef3ff;
    border: 1px solid #cdd9ff;
    padding: 16px;
    border-radius: 8px;
    margin-bottom: 24px;
}
</style>
""", unsafe_allow_html=True)

# =============================
# HÀM TIỆN ÍCH
# =============================
def gen_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def remaining_hours(time_str):
    try:
        t = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        remain = t + timedelta(hours=24) - datetime.now()
        return int(remain.total_seconds() // 3600)
    except:
        return -1

# =============================
# GOOGLE SHEET
# =============================
@st.cache_resource
def get_sheet():
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

sheet = get_sheet()

ws_xe = sheet.worksheet("Xe")
ws_ls = sheet.worksheet("Lịch sử bảo dưỡng")
ws_next = sheet.worksheet("Lịch bảo dưỡng tiếp theo")
ws_cap = sheet.worksheet("CapPhep")

df_xe = pd.DataFrame(ws_xe.get_all_records())
df_ls = pd.DataFrame(ws_ls.get_all_records())
df_next = pd.DataFrame(ws_next.get_all_records())
df_cap = pd.DataFrame(ws_cap.get_all_records())

# =============================
# TRẠNG THÁI ĐĂNG NHẬP
# =============================
if "auth" not in st.session_state:
    st.session_state.auth = None

# =============================
# ĐĂNG NHẬP
# =============================
if st.session_state.auth is None:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">Nhập mã truy cập</div>', unsafe_allow_html=True)

    code = st.text_input("Mã truy cập", type="password")

    if st.button("Xác nhận"):
        # ADMIN KHÔNG HẾT HẠN
        if code == "ADMIN":
            st.session_state.auth = {
                "code": "ADMIN",
                "bien_so": "ALL"
            }
            st.experimental_rerun()

        row = df_cap[df_cap["MaTruyCap"] == code]
        if row.empty:
            st.error("Mã truy cập không tồn tại")
        else:
            cap_time = datetime.strptime(row.iloc[0]["ThoiDiemCap"], "%Y-%m-%d %H:%M")
            if datetime.now() > cap_time + timedelta(hours=24):
                st.error("Mã truy cập đã hết hạn")
            else:
                st.session_state.auth = {
                    "code": code,
                    "bien_so": row.iloc[0]["BienSo"]
                }
                st.experimental_rerun()

    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# =============================
# QUẢN TRỊ ADMIN
# =============================
if st.session_state.auth["code"] == "ADMIN":
    st.markdown('<div class="admin-box">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">Quản trị mã truy cập</div>', unsafe_allow_html=True)

    bien_so_cap = st.selectbox(
        "Chọn biển số cần cấp quyền",
        df_xe["Biển số"].dropna().unique().tolist()
    )

    if st.button("Tạo mã truy cập"):
        new_code = gen_code()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        ws_cap.append_row([new_code, bien_so_cap, now_str])
        st.success(f"Mã mới: {new_code}")
        st.experimental_rerun()

    st.markdown("Danh sách mã còn hiệu lực")

    df_cap = pd.DataFrame(ws_cap.get_all_records())
    view = []
    for _, r in df_cap.iterrows():
        h = remaining_hours(r["ThoiDiemCap"])
        if h > 0:
            view.append({
                "Mã": r["MaTruyCap"],
                "Biển số": r["BienSo"],
                "Còn hiệu lực (giờ)": h
            })

    if view:
        st.dataframe(pd.DataFrame(view), use_container_width=True)
    else:
        st.info("Không có mã còn hiệu lực")

    ma_thu_hoi = st.text_input("Nhập mã cần thu hồi")
    if st.button("Thu hồi mã"):
        data = ws_cap.get_all_values()
        for i, r in enumerate(data[1:], start=2):
            if r[0] == ma_thu_hoi:
                ws_cap.delete_rows(i)
                st.success("Đã thu hồi")
                st.experimental_rerun()
        st.error("Không tìm thấy mã")

    st.markdown('</div>', unsafe_allow_html=True)

# =============================
# TRA CỨU XE (USER + ADMIN)
# =============================
if st.session_state.auth["bien_so"] == "ALL":
    bien_so_list = sorted(df_xe["Biển số"].dropna().unique())
else:
    bien_so_list = [st.session_state.auth["bien_so"]]

selected = st.selectbox("Chọn biển số xe", bien_so_list)

xe = df_xe[df_xe["Biển số"] == selected].iloc[0]

st.markdown('<div class="card">', unsafe_allow_html=True)
st.markdown('<div class="card-title">Thông tin xe</div>', unsafe_allow_html=True)

try:
    nam = int(float(xe["Năm sản xuất"]))
except:
    nam = "Chưa cập nhật"

st.write({
    "Biển số": xe["Biển số"],
    "Loại xe": xe["Loại xe"],
    "Năm sản xuất": nam,
    "Trạng thái": xe["Trạng thái"]
})

st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div class="card">', unsafe_allow_html=True)
st.markdown('<div class="card-title">Lịch bảo dưỡng tiếp theo</div>', unsafe_allow_html=True)

dfn = df_next[df_next["Biển số"] == selected]
if dfn.empty:
    st.warning("Chưa có dữ liệu")
else:
    st.write(dfn.iloc[0].to_dict())

st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div class="card">', unsafe_allow_html=True)
st.markdown('<div class="card-title">Lịch sử bảo dưỡng</div>', unsafe_allow_html=True)

dfh = df_ls[df_ls["Biển số"] == selected]
dfh["Chi phí"] = pd.to_numeric(dfh["Chi phí"], errors="coerce").fillna(0)
st.dataframe(dfh, use_container_width=True)

st.write(f"Tổng chi phí: {dfh['Chi phí'].sum():,.0f}".replace(",", "."))

output = BytesIO()
with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
    dfh.to_excel(writer, index=False)

st.download_button(
    "Xuất Excel",
    output.getvalue(),
    file_name=f"lich_su_{selected}.xlsx"
)

st.markdown('</div>', unsafe_allow_html=True)
