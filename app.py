import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
from io import BytesIO
from datetime import datetime, timedelta
import random
import string

st.set_page_config(page_title="Tra cứu lịch bảo dưỡng", layout="wide")

def get_remaining_hours(cap_time_str):
    try:
        cap_time = datetime.strptime(cap_time_str, "%Y-%m-%d %H:%M")
        remain = cap_time + timedelta(hours=24) - datetime.now()
        return int(remain.total_seconds() // 3600)
    except:
        return -1

def gen_access_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

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

df_xe = pd.DataFrame(sheet.worksheet("Xe").get_all_records())
df_ls = pd.DataFrame(sheet.worksheet("Lịch sử bảo dưỡng").get_all_records())
df_next = pd.DataFrame(sheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records())
df_cap = pd.DataFrame(sheet.worksheet("CapPhep").get_all_records())

st.title("Tra cứu lịch sử bảo dưỡng xe")

if "access_info" not in st.session_state:
    st.session_state.access_info = None

if st.session_state.access_info is None:
    st.subheader("Nhập mã truy cập")
    code = st.text_input("Mã truy cập", type="password")
    if st.button("Xác nhận"):
        row = df_cap[df_cap["MaTruyCap"] == code]
        if row.empty:
            st.error("Mã truy cập không tồn tại")
        else:
            cap_time = datetime.strptime(row.iloc[0]["ThoiDiemCap"], "%Y-%m-%d %H:%M")
            if datetime.now() > cap_time + timedelta(hours=24):
                st.error("Mã truy cập đã hết hạn")
            else:
                st.session_state.access_info = {
                    "code": code,
                    "bien_so": row.iloc[0]["BienSo"],
                    "cap_time": cap_time
                }
                st.experimental_rerun()
    st.stop()

if st.session_state.access_info["bien_so"] == "ALL":
    bien_so_duoc_xem = df_xe["Biển số"].dropna().unique().tolist()
else:
    bien_so_duoc_xem = [st.session_state.access_info["bien_so"]]

if st.session_state.access_info["code"] == "ADMIN":
    st.subheader("Quản trị mã truy cập")

    bien_so_cap = st.selectbox(
        "Chọn biển số cần cấp quyền",
        df_xe["Biển số"].dropna().unique().tolist()
    )

    if st.button("Tạo mã truy cập"):
        new_code, cap_time = create_access_code(sheet, bien_so_cap)
        st.success(f"Mã mới: {new_code} | Biển số: {bien_so_cap} | Cấp lúc: {cap_time}")
        st.experimental_rerun()

    st.divider()
    st.subheader("Danh sách mã còn hiệu lực")

    ws_cap = sheet.worksheet("CapPhep")
    df_cap = pd.DataFrame(ws_cap.get_all_records())

    rows = []
    for _, r in df_cap.iterrows():
        remain = get_remaining_hours(r["ThoiDiemCap"])
        if remain > 0:
            rows.append({
                "Mã": r["MaTruyCap"],
                "Biển số": r["BienSo"],
                "Cấp lúc": r["ThoiDiemCap"],
                "Còn hiệu lực (giờ)": remain
            })

    if rows:
        st.dataframe(pd.DataFrame(rows).sort_values("Còn hiệu lực (giờ)"), use_container_width=True)
    else:
        st.info("Không có mã nào còn hiệu lực")

    st.divider()
    st.subheader("Thu hồi mã")

    ma_thu_hoi = st.text_input("Nhập mã cần thu hồi")
    if st.button("Thu hồi"):
        data = ws_cap.get_all_values()
        found = False
        for i, row in enumerate(data[1:], start=2):
            if row[0] == ma_thu_hoi:
                ws_cap.delete_rows(i)
                found = True
                st.success("Đã thu hồi mã")
                st.experimental_rerun()
        if not found:
            st.error("Không tìm thấy mã")

df_xe = df_xe[df_xe["Biển số"].isin(bien_so_duoc_xem)]
df_ls = df_ls[df_ls["Biển số"].isin(bien_so_duoc_xem)]
df_next = df_next[df_next["Biển số"].isin(bien_so_duoc_xem)]

bien_so_list = sorted(bien_so_duoc_xem)

if "selected_bien_so" not in st.session_state:
    st.session_state.selected_bien_so = bien_so_list[0]

selected_bien_so = st.selectbox(
    "Chọn biển số xe",
    bien_so_list,
    index=bien_so_list.index(st.session_state.selected_bien_so)
)

st.session_state.selected_bien_so = selected_bien_so

xe_info = df_xe[df_xe["Biển số"] == selected_bien_so].iloc[0]
nam_raw = xe_info.get("Năm sản xuất", "")
try:
    nam_sx = int(float(nam_raw))
except:
    nam_sx = "Chưa cập nhật"

st.subheader("Thông tin xe")
st.write({
    "Biển số": xe_info["Biển số"],
    "Loại xe": xe_info["Loại xe"],
    "Năm sản xuất": nam_sx,
    "Trạng thái": xe_info["Trạng thái"]
})

st.subheader("Lịch bảo dưỡng tiếp theo")
df_next_f = df_next[df_next["Biển số"] == selected_bien_so]
if not df_next_f.empty:
    st.write(df_next_f.iloc[0].to_dict())
else:
    st.warning("Chưa có dữ liệu")

st.subheader("Lịch sử bảo dưỡng")

df_ls = df_ls[df_ls["Biển số"] == selected_bien_so]
df_ls["Ngày"] = pd.to_datetime(df_ls["Ngày"], errors="coerce")
df_ls = df_ls.dropna(subset=["Ngày"])
df_ls["Ngày"] = df_ls["Ngày"].dt.strftime("%d/%m/%Y")
df_ls["Chi phí"] = pd.to_numeric(df_ls["Chi phí"], errors="coerce").fillna(0)

gb = GridOptionsBuilder.from_dataframe(df_ls[["Biển số", "Ngày", "Nội dung", "Chi phí"]])
gb.configure_selection("single")
grid_options = gb.build()

grid_response = AgGrid(
    df_ls[["Biển số", "Ngày", "Nội dung", "Chi phí"]],
    gridOptions=grid_options,
    update_mode=GridUpdateMode.SELECTION_CHANGED,
    allow_unsafe_jscode=True
)

selected = grid_response.get("selected_rows", [])
if selected:
    st.subheader("Nội dung chi tiết")
    st.write(selected[0]["Nội dung"])

st.subheader("Tổng chi phí")
st.write(f"{df_ls['Chi phí'].sum():,.0f} VND".replace(",", "."))

output = BytesIO()
with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
    df_ls.to_excel(writer, index=False)

st.download_button(
    label="Xuất Excel",
    data=output.getvalue(),
    file_name=f"lich_su_{selected_bien_so}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
