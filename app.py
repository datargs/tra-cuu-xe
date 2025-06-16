import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
from io import BytesIO
import datetime
import base64

# Load credentials từ Streamlit secrets
creds_dict = st.secrets["gcp_service_account"]
credentials = service_account.Credentials.from_service_account_info(creds_dict)

# Kết nối Google Sheet
gc = gspread.authorize(credentials)
sheet = gc.open_by_key("1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")

# Load dữ liệu từ các sheet
ws_xe = sheet.worksheet("Xe")
ws_ls = sheet.worksheet("Lịch sử bảo dưỡng")
ws_tiep = sheet.worksheet("Lịch bảo dưỡng tiếp theo")

df_xe = pd.DataFrame(ws_xe.get_all_records())
df_ls = pd.DataFrame(ws_ls.get_all_records())
df_tiep = pd.DataFrame(ws_tiep.get_all_records())

# Tiêu đề
st.title("📋 Tra cứu bảo dưỡng xe")

# Chọn biển số
bien_so_list = df_xe["Biển số"].unique().tolist()
bien_so = st.selectbox("Chọn biển số xe:", sorted(bien_so_list))

# Hiển thị thông tin xe
df_info = df_xe[df_xe["Biển số"] == bien_so]
if not df_info.empty:
    loai_xe = df_info.iloc[0]["Loại xe"]
    nam_sx = int(df_info.iloc[0]["Năm sản xuất"])
    trang_thai = df_info.iloc[0]["Trạng thái"]
    st.markdown(f"#### 🚗 {bien_so} — {loai_xe} — {nam_sx} — {trang_thai}")

# Hiển thị lịch bảo dưỡng tiếp theo
df_next = df_tiep[df_tiep["Biển số"] == bien_so]
if not df_next.empty:
    st.subheader("📅 Lịch bảo dưỡng tiếp theo:")
    st.write(df_next.iloc[0].to_dict())
else:
    st.warning("Chưa có lịch bảo dưỡng tiếp theo")

# Lịch sử bảo dưỡng
df_bs = df_ls[df_ls["Biển số"] == bien_so].copy()

# Định dạng ngày
try:
    df_bs["Ngày"] = pd.to_datetime(df_bs["Ngày"], dayfirst=True)
except:
    pass

# Bộ lọc ngày
df_bs = df_bs.sort_values("Ngày", ascending=False)
st.markdown("### 📜 Lịch sử bảo dưỡng")
col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    tu_ngay = st.date_input("Từ ngày", value=None, key="tu_ngay")
with col2:
    den_ngay = st.date_input("Đến ngày", value=None, key="den_ngay")
with col3:
    xem = st.button("Xem")

if tu_ngay and den_ngay and tu_ngay > den_ngay:
    st.error("❌ Từ ngày phải nhỏ hơn hoặc bằng Đến ngày")
else:
    if tu_ngay:
        df_bs = df_bs[df_bs["Ngày"] >= pd.to_datetime(tu_ngay)]
    if den_ngay:
        df_bs = df_bs[df_bs["Ngày"] <= pd.to_datetime(den_ngay)]

# Hiển thị bảng đẹp với AgGrid
if not df_bs.empty:
    df_bs["Chi phí"] = df_bs["Chi phí"].astype(str)
    df_bs["Ngày hiển thị"] = df_bs["Ngày"].dt.strftime("%d/%m/%Y")
    df_bs["Nội dung ngắn"] = df_bs["Nội dung"].str.wrap(60).str.split("\n").str[0] + "..."

    cell_style_wrap = JsCode("""
    function(params) {
        return {
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis'
        }
    }
    """)

    gb = GridOptionsBuilder.from_dataframe(df_bs[["Ngày hiển thị", "Nội dung ngắn", "Chi phí", "Ghi chú"]])
    gb.configure_column("Ngày hiển thị", header_name="Ngày", cellStyle=cell_style_wrap)
    gb.configure_column("Chi phí", cellStyle=cell_style_wrap)
    gb.configure_column("Nội dung ngắn", header_name="Nội dung", tooltipField="Nội dung ngắn", cellStyle=cell_style_wrap)
    gb.configure_column("Ghi chú", cellStyle=cell_style_wrap)
    gridOptions = gb.build()

    st.markdown("### 📊 Chi tiết bảo dưỡng")
    AgGrid(
        df_bs[["Ngày hiển thị", "Nội dung ngắn", "Chi phí", "Ghi chú"]],
        gridOptions=gridOptions,
        update_mode=GridUpdateMode.NO_UPDATE,
        fit_columns_on_grid_load=True,
        theme="alpine",
        height=min(600, 40 * len(df_bs) + 100),
    )

    # Tổng chi phí
    try:
        tong = pd.to_numeric(df_bs["Chi phí"], errors="coerce").sum()
        st.success(f"💰 Tổng chi phí: {tong:,.0f} đ")
    except:
        pass

    # Xuất Excel
    to_excel = st.button("📤 Xuất Excel")
    if to_excel:
        out = BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as writer:
            df_bs.to_excel(writer, index=False, sheet_name="LichSu")
            writer.close()
        st.download_button(
            label="Tải file Excel",
            data=out.getvalue(),
            file_name=f"lich_su_{bien_so}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    st.info("Không có dữ liệu lịch sử bảo dưỡng.")
