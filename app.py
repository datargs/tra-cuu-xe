import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
from st_aggrid import AgGrid, GridOptionsBuilder
from io import BytesIO
import datetime

# Thiết lập cấu hình trang
st.set_page_config(page_title="Tra cứu bảo dưỡng xe", layout="wide")

# Load credentials từ secrets
creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
gc = gspread.authorize(creds)

# Mở Google Sheet
sheet = gc.open_by_key("1vVwCCoKCuRZZLx6QrprgKM8b067F-p8QKYVbkc1yavo")
ws_xe = sheet.worksheet("Xe")
ws_ls = sheet.worksheet("Lịch sử bảo dưỡng")
ws_next = sheet.worksheet("Lịch bảo dưỡng tiếp theo")

# Đọc dữ liệu
df_xe = pd.DataFrame(ws_xe.get_all_records())
df_ls = pd.DataFrame(ws_ls.get_all_records())
df_next = pd.DataFrame(ws_next.get_all_records())

# Tiêu đề trang
st.title("🚗 Tra cứu lịch sử bảo dưỡng xe")

# Lấy danh sách biển số không trùng
list_bien_so = sorted(df_xe["Biển số"].dropna().unique().tolist())
selected_plate = st.selectbox("🔍 Chọn biển số xe", options=list_bien_so)

# Hiển thị thông tin xe từ bảng "Xe"
info = df_xe[df_xe["Biển số"] == selected_plate].squeeze()
st.markdown(f"""
**Biển số:** {info['Biển số']}  
**Loại xe:** {info['Loại xe']}  
**Năm sản xuất:** {int(info['Năm sản xuất'])}  
**Trạng thái:** {info['Trạng thái']}
""")

# Hiển thị lịch bảo dưỡng tiếp theo
df_next_plate = df_next[df_next["Biển số"] == selected_plate]
st.subheader("📅 Lịch bảo dưỡng tiếp theo:")

if not df_next_plate.empty:
    row = df_next_plate.iloc[0]
    st.markdown(f"**Dự kiến lần tiếp theo:** {row['Dự kiến lần tiếp theo']}  \n**Gợi ý nội dung:** {row['Gợi ý nội dung']}")
else:
    st.warning("Chưa có lịch bảo dưỡng tiếp theo.")

# Format ngày
df_ls["Ngày"] = pd.to_datetime(df_ls["Ngày"], errors='coerce')
df_ls = df_ls[df_ls["Biển số"] == selected_plate].copy()
df_ls["Ngày hiển thị"] = df_ls["Ngày"].dt.strftime("%d/%m/%Y")

# Bộ lọc ngày
st.subheader("📂 Lọc theo thời gian")

col1, col2, col3 = st.columns([2, 2, 1])

with col1:
    tu_ngay = st.date_input("Từ ngày", value=None, format="DD/MM/YYYY")

with col2:
    den_ngay = st.date_input("Đến ngày", value=None, format="DD/MM/YYYY")

with col3:
    if st.button("Xem"):
        if tu_ngay and den_ngay and tu_ngay > den_ngay:
            st.error("❌ Ngày bắt đầu phải nhỏ hơn hoặc bằng ngày kết thúc.")
        else:
            if tu_ngay:
                df_ls = df_ls[df_ls["Ngày"] >= pd.to_datetime(tu_ngay)]
            if den_ngay:
                df_ls = df_ls[df_ls["Ngày"] <= pd.to_datetime(den_ngay)]

# Hiển thị bảng lịch sử bảo dưỡng
st.subheader("🛠️ Lịch sử bảo dưỡng")

if not df_ls.empty:
    gb = GridOptionsBuilder.from_dataframe(df_ls[["Ngày hiển thị", "Nội dung", "Chi phí", "Ghi chú"]])
    gb.configure_column("Nội dung", wrapText=True, autoHeight=True)
    gb.configure_column("Ghi chú", wrapText=True, autoHeight=True)
    gb.configure_grid_options(domLayout='normal')
    gridOptions = gb.build()

    AgGrid(df_ls[["Ngày hiển thị", "Nội dung", "Chi phí", "Ghi chú"]],
           gridOptions=gridOptions,
           fit_columns_on_grid_load=True,
           height=(len(df_ls)*35 + 60),
           theme='streamlit')

    # Tổng chi phí
    st.markdown(f"**💰 Tổng chi phí:** {df_ls['Chi phí'].sum():,.0f} VND")
else:
    st.info("Không có dữ liệu bảo dưỡng phù hợp.")

# Xuất Excel
st.subheader("📤 Xuất dữ liệu")

if st.button("Xuất Excel"):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_ls.to_excel(writer, index=False, sheet_name="Lịch sử bảo dưỡng")
    st.download_button(label="📥 Tải Excel", data=output.getvalue(),
                       file_name=f"lich_su_bao_duong_{selected_plate}.xlsx", mime="application/vnd.ms-excel")
