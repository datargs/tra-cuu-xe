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
ADMIN_KEY = "admin"
def now_vn():
    return datetime.utcnow() + timedelta(hours=7)

def get_remaining_time(cap_time_str):
    try:
        cap_time = datetime.strptime(cap_time_str, "%Y-%m-%d %H:%M")
        remain = (cap_time + timedelta(hours=24)) - now_vn()

        if remain.total_seconds() <= 0:
            return "Hết hạn"

        total_minutes = int(remain.total_seconds() // 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60

        return f"Còn {hours} giờ {minutes} phút"
    except:
        return "—"

def gen_access_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# ⚙️ Cấu hình Streamlit (PHẢI đặt ở đầu!)
st.set_page_config(page_title="Tra cứu lịch bảo dưỡng", layout="wide")
is_mobile = st.session_state.get("is_mobile_width", 1200) < 700

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
@st.cache_data(ttl=300)
def load_sheet_data():
    sheet = get_gsheet()
    return {
        "xe": pd.DataFrame(sheet.worksheet("Xe").get_all_records()),
        "ls": pd.DataFrame(sheet.worksheet("Lịch sử bảo dưỡng").get_all_records()),
        "next": pd.DataFrame(sheet.worksheet("Lịch bảo dưỡng tiếp theo").get_all_records()),
        "cap": pd.DataFrame(sheet.worksheet("CapPhep").get_all_records()),
    }

@st.cache_data(ttl=300)
def load_cap_phep():
    sheet = get_gsheet()
    return pd.DataFrame(sheet.worksheet("CapPhep").get_all_records())

def create_access_code(sheet, bien_so):
    ws = sheet.worksheet("CapPhep")

    new_code = gen_access_code()
    now_str = now_vn().strftime("%Y-%m-%d %H:%M")

    ws.append_row([new_code, bien_so, now_str])

    return new_code, now_str

st.title("Tra cứu lịch sử bảo dưỡng xe")
# 🔐 KIỂM TRA MÃ TRUY CẬP (có hạn 24h)
if "access_info" not in st.session_state:
    st.session_state.access_info = None

if st.session_state.access_info is None:
    st.markdown("## Nhập mã truy cập")

    code = st.text_input("Mã truy cập", type="password")
    if st.button("Xác nhận"):

        if code == ADMIN_KEY:
            st.session_state.access_info = {
                "code": ADMIN_KEY,
                "bien_so": "ALL",
                "cap_time": None
            }
            st.experimental_rerun()

        # 🔐 Mã thường → load riêng CapPhep
        df_cap_tmp = load_cap_phep()
        row = df_cap_tmp[df_cap_tmp["MaTruyCap"] == code]

        if row.empty:
            st.error("❌ Mã truy cập không tồn tại")
        else:
            cap_time = datetime.strptime(
                row.iloc[0]["ThoiDiemCap"], "%Y-%m-%d %H:%M"
            )

            if now_vn() > cap_time + timedelta(hours=24):
                st.error("Mã truy cập đã hết hạn (24h)")
            else:
                st.session_state.access_info = {
                    "code": code,
                    "bien_so": row.iloc[0]["BienSo"],
                    "cap_time": cap_time
                }
                st.experimental_rerun()

    st.stop()

data = load_sheet_data()

df_xe = data["xe"]
df_ls = data["ls"]
df_next = data["next"]
df_cap = data["cap"]

# 🔎 Xác định biển số được phép xem
if st.session_state.access_info["bien_so"] == "ALL":
    bien_so_duoc_xem = df_xe["Biển số"].dropna().unique().tolist()
else:
    bien_so_duoc_xem = [st.session_state.access_info["bien_so"]]
# 🛠️ KHU VỰC QUẢN TRỊ – CHỈ admin
if st.session_state.access_info["code"] == ADMIN_KEY:
    tab_admin, tab_user = st.tabs(["Quản lý mã đăng nhập", "Tra cứu xe"])
else:
    tab_user, = st.tabs(["Tra cứu xe"])
if st.session_state.access_info["code"] == ADMIN_KEY:
    with tab_admin:
        st.markdown("## Quản lý mã đăng nhập")

        ws_cap = sheet.worksheet("CapPhep")
        df_cap = pd.DataFrame(ws_cap.get_all_records())

        if df_cap.empty:
            st.info("Chưa có mã truy cập nào.")
        else:
            st.markdown("### Danh sách mã truy cập (trừ admin – vĩnh viễn)")
            # ===== HEADER CỘT =====
            h1, h2, h3, h4, h5 = st.columns([2, 2, 2, 2, 1])
            h1.markdown("**Mã truy cập**")
            h2.markdown("**Biển số**")
            h3.markdown("**Thời điểm cấp**")
            h4.markdown("**Thời gian còn lại**")
            h5.markdown("**Thao tác**")
            st.divider()

            for idx, r in df_cap[df_cap["MaTruyCap"] != ADMIN_KEY].iterrows():
                col1, col2, col3, col4, col5 = st.columns([2, 2, 2, 2, 1])

                remain_hours = get_remaining_hours(r["ThoiDiemCap"])

                col1.write(r["MaTruyCap"])
                col2.write(r["BienSo"])
                col3.write(r["ThoiDiemCap"])
                col4.write(get_remaining_time(r["ThoiDiemCap"]))
                # 🔥 NÚT THU HỒI THEO DÒNG
                if r["MaTruyCap"] != ADMIN_KEY:
                    if col5.button("❌ Thu hồi", key=f"revoke_{r['MaTruyCap']}"):
                        data_all = ws_cap.get_all_values()
                        for i, row in enumerate(data_all[1:], start=2):
                            if row[0] == r["MaTruyCap"]:
                                ws_cap.delete_rows(i)
                                st.warning(
                                    f"Đã thu hồi mã {r['MaTruyCap']}. Người dùng sẽ mất quyền khi reload."
                                )
                                st.cache_data.clear()
                                st.experimental_rerun()
        st.divider()
        st.markdown("### Tạo mã truy cập mới (24h)")

        bien_so_cap = st.selectbox(
            "Chọn biển số cần cấp quyền:",
            df_xe["Biển số"].dropna().unique().tolist()
        )

        if st.button("Tạo mã truy cập"):
            new_code, cap_time = create_access_code(sheet, bien_so_cap)
            st.success(f"""
            Đã tạo mã thành công  
            **Mã:** `{new_code}`  
            **Biển số:** {bien_so_cap}  
            **Cấp lúc:** {cap_time}  
            **Hiệu lực:** 24 giờ
            """)
            st.cache_data.clear()
            st.experimental_rerun()
with tab_user:
    # 🔒 Lọc dữ liệu theo quyền truy cập
    df_xe = df_xe[df_xe["Biển số"].isin(bien_so_duoc_xem)]
    df_ls = df_ls[df_ls["Biển số"].isin(bien_so_duoc_xem)]
    df_next = df_next[df_next["Biển số"].isin(bien_so_duoc_xem)]

    bien_so_list_sorted = sorted(bien_so_duoc_xem)

    # Khởi tạo session_state nếu chưa có
    if "selected_bien_so" not in st.session_state:
        st.session_state.selected_bien_so = bien_so_list_sorted[0]

    selected_bien_so = st.selectbox(
        "Chọn biển số xe:",
        bien_so_list_sorted,
        index=bien_so_list_sorted.index(st.session_state.selected_bien_so)
    )

    st.session_state.selected_bien_so = selected_bien_so
    # 📄 Hiển thị thông tin xe
    xe_info = df_xe[df_xe["Biển số"] == selected_bien_so].iloc[0]
    nam_sx_raw = xe_info.get("Năm sản xuất", "")
    try:
        nam_sx = int(float(nam_sx_raw))
    except:
        nam_sx = "Chưa cập nhật"

    st.markdown("### Thông tin xe")
    st.markdown(f"""
    <table style="border-collapse: collapse; width: 100%;">
      <tr><td><b>🚗 Biển số</b></td><td>{xe_info['Biển số']}</td></tr>
      <tr><td><b>🔧 Loại xe</b></td><td>{xe_info['Loại xe']}</td></tr>
      <tr><td><b>📅 Năm sản xuất</b></td><td>{nam_sx}</td></tr>
      <tr><td><b>📍 Trạng thái</b></td><td>{xe_info['Trạng thái']}</td></tr>
    </table>
    """, unsafe_allow_html=True)

    # 📅 Lịch bảo dưỡng tiếp theo
    st.markdown("### Lịch bảo dưỡng tiếp theo")
    df_next_filtered = df_next[df_next["Biển số"] == selected_bien_so]
    if not df_next_filtered.empty:
        st.write(f"- **Dự kiến:** {df_next_filtered.iloc[0]['Dự kiến lần tiếp theo']}")
        st.write(f"- **Gợi ý nội dung:** {df_next_filtered.iloc[0]['Gợi ý nội dung']}")
    else:
        st.warning("Chưa có lịch bảo dưỡng tiếp theo.")

    # 📆 Lịch sử bảo dưỡng
    st.markdown("### Lịch sử bảo dưỡng")
    df_ls_view = df_ls[df_ls["Biển số"] == selected_bien_so].copy()

    df_ls_view["Ngày"] = pd.to_datetime(df_ls_view["Ngày"], errors="coerce")
    df_ls_view = df_ls_view.dropna(subset=["Ngày"])

    df_ls_view["Ngày"] = df_ls_view["Ngày"].dt.strftime("%d/%m/%Y")
    df_ls_view["Chi phí"] = pd.to_numeric(df_ls_view["Chi phí"], errors="coerce").fillna(0)
    df_ls_view["Chi phí hiển thị"] = df_ls_view["Chi phí"].apply(lambda x: f"{x:,.0f}".replace(",", "."))
    if is_mobile:
        st.markdown("#### Lịch sử bảo dưỡng")

        # 👉 Bảng mobile: Ngày + Nội dung + Chi phí
        st.dataframe(
            df_ls_view[["Ngày", "Nội dung", "Chi phí hiển thị"]],
            use_container_width=True,
            hide_index=True
        )


        # Chi tiết từng dòng
        st.markdown("#### 🔍 Chi tiết")
        for _, r in df_ls_view.iterrows():
            with st.expander(f"{r['Ngày']} – {r['Chi phí hiển thị']} VND"):
                st.write(r["Nội dung"])
    else:
        cols = ["Biển số", "Ngày", "Nội dung", "Chi phí hiển thị"]

        gb = GridOptionsBuilder.from_dataframe(df_ls_view[cols])

        gb.configure_default_column(
            wrapText=True,
            autoHeight=True,
            resizable=True,
            sortable=True
        )

        gb.configure_column("Biển số", width=120)
        gb.configure_column("Ngày", width=120)
        gb.configure_column("Nội dung", flex=1)
        gb.configure_column(
            "Chi phí hiển thị",
            headerName="Chi phí",
            width=140
        )

        AgGrid(
            df_ls_view[cols],
            gridOptions=gb.build(),
            update_mode=GridUpdateMode.NO_UPDATE,
            fit_columns_on_grid_load=True,
            height=350
        )

    # 💰 Tổng chi phí
    tong_chi_phi = df_ls_view["Chi phí"].sum()
    st.markdown(f"#### Tổng chi phí: `{tong_chi_phi:,.0f} VND`".replace(",", "."))

    # 📥 Xuất Excel
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_ls_view[["Biển số", "Ngày", "Nội dung", "Chi phí"]].to_excel(
            writer, index=False, sheet_name="LichSuBaoDuong"
        )

    st.download_button(
        "Xuất Excel",
        data=output.getvalue(),
        file_name=f"lich_su_bao_duong_{selected_bien_so}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

