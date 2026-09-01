from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO
import os
import random
import re
import sqlite3
import string
import time

from flask import (
    abort,
    Flask,
    Response,
    flash,
    has_request_context,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)
import pandas as pd


ADMIN_KEY = os.environ.get("TRACUUXE_ADMIN_KEY", "")
ACCESS_TTL_HOURS = 24
INACTIVITY_TIMEOUT_MINUTES = 15
CACHE_TTL_SECONDS = 900
DATA_DB_PATH = os.environ.get("TRACUUXE_DB_PATH", os.path.join(os.path.dirname(__file__), "tracuuxe.sqlite3"))

SHEET_XE = "Xe"
SHEET_HISTORY = "Lịch sử bảo dưỡng"
SHEET_NEXT = "Lịch bảo dưỡng tiếp theo"
SHEET_ACCESS = "CapPhep"

XE_COLUMNS = ["Biển số", "Loại xe", "Năm sản xuất", "Trạng thái"]
HISTORY_COLUMNS = ["ID", "Biển số", "Ngày", "Nội dung", "Chi phí", "LoaiChiPhi"]
NEXT_COLUMNS = ["Biển số", "Lịch bảo dưỡng gần nhất", "Dự kiến lần tiếp theo", "Gợi ý nội dung", "Ngày đăng kiểm", "Hạn đăng kiểm đến", "Chi phí thay dầu", "Chi phí đăng kiểm"]
ACCESS_COLUMNS = ["MaTruyCap", "BienSo", "ThoiDiemCap", "ThoiHanGio"]
VEHICLE_STATUS_ACTIVE = "Đang hoạt động"
VEHICLE_STATUS_INACTIVE = "Ngừng hoạt động"
COST_TYPE_REPAIR = "repair"
COST_TYPE_PERIODIC = "periodic"
COST_TYPE_LABELS = {
    COST_TYPE_REPAIR: "Sửa chữa",
    COST_TYPE_PERIODIC: "Bảo dưỡng định kỳ",
}
AUDIT_ACTION_LABELS = {
    "login": "Đăng nhập",
    "logout": "Đăng xuất",
    "create_access_code": "Tạo mã truy cập",
    "revoke_access_code": "Thu hồi mã",
    "extend_access_code": "Gia hạn mã",
    "upsert_vehicle": "Thêm/Sửa xe",
    "deactivate_vehicle": "Ngừng hoạt động xe",
    "delete_vehicle": "Xóa xe",
    "save_next_service": "Lưu lịch bảo dưỡng/đăng kiểm",
    "update_next_log": "Sửa dòng lịch kế hoạch",
    "delete_next_log": "Xóa dòng lịch kế hoạch",
    "delete_next_service": "Xóa lịch bảo dưỡng/đăng kiểm",
    "add_history_record": "Thêm lịch sử bảo dưỡng",
    "update_history_record": "Sửa lịch sử bảo dưỡng",
    "delete_history_record": "Xóa lịch sử bảo dưỡng",
}


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)

_cache = {"sheets": {}}

BAD_SCAN_PREFIXES = (
    "/wp-",
    "/wordpress",
    "/vendor/",
    "/phpmailer/",
    "/adminer",
    "/phpmyadmin",
    "/pma",
    "/cgi-bin/",
    "/.git",
    "/.svn",
    "/.hg",
)
BAD_SCAN_SUFFIXES = (
    ".php",
    ".php3",
    ".php4",
    ".php5",
    ".php7",
    ".phtml",
    ".asp",
    ".aspx",
    ".jsp",
)
BAD_SCAN_PATHS = {
    "/.env",
    "/.env.local",
    "/xmlrpc.php",
    "/config.php",
    "/composer.json",
    "/composer.lock",
}


@app.before_request
def block_common_scanners():
    path = request.path.lower()
    if (
        path in BAD_SCAN_PATHS
        or path.startswith(BAD_SCAN_PREFIXES)
        or path.endswith(BAD_SCAN_SUFFIXES)
    ):
        abort(403)


@app.route("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")


@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.ico")


@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return app.send_static_file("favicon-32.png")


def now_vn():
    return datetime.utcnow() + timedelta(hours=7)


def parse_last_activity(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def touch_last_activity():
    session["last_activity"] = now_vn().isoformat(timespec="seconds")


def current_actor_code():
    if not has_request_context():
        return None
    info = session.get("access_info") or {}
    code = str(info.get("code", "")).strip()
    return code or None


def audit_log(action, detail="", plate=None):
    init_db()
    ip_addr = ""
    if has_request_context():
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        real_ip = request.headers.get("X-Real-IP", "")
        cf_ip = request.headers.get("CF-Connecting-IP", "")
        ip_addr = (
            cf_ip
            or real_ip
            or (forwarded_for.split(",")[0].strip() if forwarded_for else "")
            or (request.remote_addr or "")
        )
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs (created_at, actor_code, action, plate, detail, ip_addr)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                now_vn().strftime("%Y-%m-%d %H:%M"),
                current_actor_code(),
                str(action or "").strip(),
                normalize_plate(plate) if plate else None,
                str(detail or "").strip(),
                ip_addr,
            ),
        )


def db_connect():
    os.makedirs(os.path.dirname(DATA_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DATA_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cars (
                plate TEXT PRIMARY KEY,
                car_type TEXT,
                manufacture_year TEXT,
                status TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS next_services (
                plate TEXT PRIMARY KEY,
                last_service TEXT,
                next_due TEXT,
                suggestion TEXT,
                registry_due TEXT,
                registry_odo TEXT,
                registry_date TEXT,
                oil_cost REAL,
                registry_cost REAL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS next_service_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL,
                last_service TEXT,
                next_due TEXT,
                suggestion TEXT,
                registry_date TEXT,
                registry_due TEXT,
                oil_cost REAL,
                registry_cost REAL,
                saved_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS service_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL,
                service_date TEXT,
                content TEXT,
                cost REAL DEFAULT 0,
                cost_type TEXT DEFAULT 'repair',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS access_codes (
                code TEXT PRIMARY KEY,
                plates TEXT NOT NULL,
                created_at TEXT NOT NULL,
                ttl_hours INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor_code TEXT,
                action TEXT NOT NULL,
                plate TEXT,
                detail TEXT,
                ip_addr TEXT
            );
            """
        )
        history_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(service_history)").fetchall()
        }
        if "cost_type" not in history_columns:
            conn.execute("ALTER TABLE service_history ADD COLUMN cost_type TEXT DEFAULT 'repair'")
        next_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(next_services)").fetchall()
        }
        if "registry_odo" not in next_columns:
            conn.execute("ALTER TABLE next_services ADD COLUMN registry_odo TEXT")
        if "registry_date" not in next_columns:
            conn.execute("ALTER TABLE next_services ADD COLUMN registry_date TEXT")
        if "last_service" not in next_columns:
            conn.execute("ALTER TABLE next_services ADD COLUMN last_service TEXT")
        audit_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(audit_logs)").fetchall()
        }
        if not audit_columns:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    actor_code TEXT,
                    action TEXT NOT NULL,
                    plate TEXT,
                    detail TEXT,
                    ip_addr TEXT
                );
                """
            )

def normalize_plate(value):
    return re.sub(r"\s+", "", str(value or "").strip().upper())


def normalize_vehicle_status(value):
    text = str(value or "").strip().lower()
    if text in ("ngừng hoạt động", "ngung hoat dong"):
        return VEHICLE_STATUS_INACTIVE
    return VEHICLE_STATUS_ACTIVE


def normalize_history_cost_type(value):
    text = str(value or "").strip().lower()
    if text in (COST_TYPE_PERIODIC, "bao duong dinh ky", "bảo dưỡng định kỳ", "bd dinh ky", "bd định kỳ"):
        return COST_TYPE_PERIODIC
    return COST_TYPE_REPAIR


def history_cost_label(value):
    return COST_TYPE_LABELS.get(normalize_history_cost_type(value), COST_TYPE_LABELS[COST_TYPE_REPAIR])


def active_vehicle_df(df):
    if df.empty or "Trạng thái" not in df.columns:
        return df
    return df[df["Trạng thái"].apply(normalize_vehicle_status) != VEHICLE_STATUS_INACTIVE].copy()


def parse_money_value(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if pd.isna(value):
                return 0
        except Exception:
            pass
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0
    digits = re.sub(r"\D", "", text)
    if not digits:
        return 0
    try:
        return float(digits)
    except Exception:
        return 0


def clear_cache(sheet_name=None):
    if sheet_name:
        _cache["sheets"].pop(sheet_name, None)
    else:
        _cache["sheets"].clear()


def worksheet_df(name, columns=None):
    cached = _cache["sheets"].get(name)
    if cached and time.time() - cached["time"] < CACHE_TTL_SECONDS:
        return cached["df"].copy()

    init_db()
    with db_connect() as conn:
        if name == SHEET_XE:
            df = pd.read_sql_query(
                """
                SELECT
                    plate AS 'Biển số',
                    car_type AS 'Loại xe',
                    manufacture_year AS 'Năm sản xuất',
                    status AS 'Trạng thái'
                FROM cars
                ORDER BY plate
                """,
                conn,
            )
        elif name == SHEET_HISTORY:
            df = pd.read_sql_query(
                """
                SELECT
                    id AS 'ID',
                    plate AS 'Biển số',
                    service_date AS 'Ngày',
                    content AS 'Nội dung',
                    cost AS 'Chi phí',
                    COALESCE(cost_type, 'repair') AS 'LoaiChiPhi'
                FROM service_history
                ORDER BY service_date DESC, id DESC
                """,
                conn,
            )
        elif name == SHEET_NEXT:
            df = pd.read_sql_query(
                """
                SELECT
                    plate AS 'Biển số',
                    last_service AS 'Lịch bảo dưỡng gần nhất',
                    next_due AS 'Dự kiến lần tiếp theo',
                    suggestion AS 'Gợi ý nội dung',
                    registry_date AS 'Ngày đăng kiểm',
                    registry_due AS 'Hạn đăng kiểm đến',
                    oil_cost AS 'Chi phí thay dầu',
                    registry_cost AS 'Chi phí đăng kiểm'
                FROM next_services
                ORDER BY plate
                """,
                conn,
            )
        elif name == SHEET_ACCESS:
            df = pd.read_sql_query(
                """
                SELECT
                    code AS 'MaTruyCap',
                    plates AS 'BienSo',
                    created_at AS 'ThoiDiemCap',
                    ttl_hours AS 'ThoiHanGio'
                FROM access_codes
                ORDER BY created_at DESC
                """,
                conn,
            )
        else:
            df = pd.DataFrame(columns=columns or [])

    if df.empty and columns:
        df = pd.DataFrame(columns=columns)
    _cache["sheets"][name] = {"time": time.time(), "df": df}
    return df.copy()


def load_data(keys=None):
    sheet_map = {
        "xe": (SHEET_XE, None),
        "history": (SHEET_HISTORY, None),
        "next": (SHEET_NEXT, None),
        "access": (SHEET_ACCESS, ["MaTruyCap", "BienSo", "ThoiDiemCap", "ThoiHanGio"]),
    }
    selected_keys = keys or sheet_map.keys()
    return {key: worksheet_df(*sheet_map[key]) for key in selected_keys}


def parse_cap_time(value):
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M")


def parse_cap_time_safe(value):
    try:
        return parse_cap_time(value)
    except Exception:
        return datetime.min


def format_datetime_display(value):
    try:
        return parse_cap_time(value).strftime("%d/%m/%Y %H:%M")
    except Exception:
        try:
            parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
            if pd.isna(parsed):
                return str(value or "")
            return parsed.strftime("%d/%m/%Y %H:%M")
        except Exception:
            return str(value or "")


def gen_access_code(length=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def parse_ttl_hours(value):
    try:
        hours = int(float(value))
        return max(1, min(hours, 24 * 30))
    except Exception:
        return ACCESS_TTL_HOURS


def remaining_text(value, ttl_hours=ACCESS_TTL_HOURS):
    try:
        remain = parse_cap_time(value) + timedelta(hours=parse_ttl_hours(ttl_hours)) - now_vn()
        if remain.total_seconds() <= 0:
            return "Hết hạn"
        minutes = int(remain.total_seconds() // 60)
        return f"Còn {minutes // 60} giờ {minutes % 60} phút"
    except Exception:
        return "-"


def access_is_active(value, ttl_hours=ACCESS_TTL_HOURS):
    try:
        return now_vn() <= parse_cap_time(value) + timedelta(hours=parse_ttl_hours(ttl_hours))
    except Exception:
        return False


def current_access():
    return session.get("access_info")


def is_admin():
    info = current_access()
    return bool(ADMIN_KEY and info and info.get("code") == ADMIN_KEY)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        info = current_access()
        if not info:
            return redirect(url_for("login"))

        if ADMIN_KEY and info.get("code") == ADMIN_KEY:
            return view(*args, **kwargs)

        try:
            cap_time = parse_cap_time(info.get("cap_time"))
        except Exception:
            session.clear()
            flash("Phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại.", "error")
            return redirect(url_for("login"))

        if now_vn() > cap_time + timedelta(hours=parse_ttl_hours(info.get("ttl_hours"))):
            session.clear()
            flash("Mã truy cập đã hết hạn.", "error")
            return redirect(url_for("login"))

        last_activity = parse_last_activity(session.get("last_activity"))
        if last_activity and now_vn() - last_activity > timedelta(minutes=INACTIVITY_TIMEOUT_MINUTES):
            session.clear()
            flash("Bạn đã tự động đăng xuất do không hoạt động trong 15 phút.", "error")
            return redirect(url_for("login"))

        touch_last_activity()
        return view(*args, **kwargs)

    return wrapped


def format_vnd(value):
    try:
        return f"{int(float(value)):,.0f}".replace(",", ".") + " VND"
    except Exception:
        return "Chưa cập nhật"


def format_money_input(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    try:
        amount = int(float(value))
        return f"{amount:,}".replace(",", ".")
    except Exception:
        return ""


def clean_history_content(value):
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return ""

    spelling_fixes = [
        (r"\bphin cách nhiệt\b", "phim cách nhiệt"),
        (r"\bdán phin\b", "dán phim"),
        (r"\bsăn đúc\b", "sàn đúc"),
        (r"\bsan duc\b", "sàn đúc"),
        (r"\bđinh kỳ\b", "định kỳ"),
        (r"\bdinh kỳ\b", "định kỳ"),
        (r"\bđịnh kì\b", "định kỳ"),
        (r"\bbão dưỡng\b", "bảo dưỡng"),
        (r"\bbảo dưởng\b", "bảo dưỡng"),
        (r"\bkiễm tra\b", "kiểm tra"),
        (r"\bhộng hút\b", "họng hút"),
    ]

    def fix_spelling(item):
        for pattern, replacement in spelling_fixes:
            item = re.sub(pattern, replacement, item, flags=re.IGNORECASE)
        return item

    def clean_item(item):
        item = re.sub(r"\s+", " ", str(item or "")).strip(" -+;,.")
        item = re.sub(r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*", "", item).strip()
        item = re.sub(r"\bSC\b", "SỬA CHỮA", item, flags=re.IGNORECASE)
        item = re.sub(r"\b(?:DBĐK|BDĐK|DBDK|BDDK)\b", "BẢO DƯỠNG ĐỊNH KỲ", item, flags=re.IGNORECASE)
        item = fix_spelling(item)
        return item

    parts = [clean_item(part) for part in re.split(r"\s*(?:\+|\r?\n)+\s*", text)]
    parts = [part for part in parts if part]
    return "\n".join(parts) if parts else clean_item(text)


def display_history_content(value):
    lines = [line.strip() for line in clean_history_content(value).splitlines() if line.strip()]
    return "\n".join(f"- {line}" for line in lines)


def normalize_year(value):
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return "Chưa cập nhật"
    match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if match:
        return match.group(1)
    try:
        numeric = float(text)
        if 1900 <= numeric <= 2100:
            return str(int(numeric))
        if numeric > 25000:
            parsed_serial = pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")
            if not pd.isna(parsed_serial):
                return str(parsed_serial.year)
    except Exception:
        pass
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if not pd.isna(parsed):
        return str(parsed.year)
    return "Chưa cập nhật"


def parse_user_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    iso_date_match = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", text)
    if iso_date_match:
        year = int(iso_date_match.group(1))
        month = int(iso_date_match.group(2))
        day = int(iso_date_match.group(3))
        try:
            return pd.Timestamp(year=year, month=month, day=day)
        except ValueError:
            return None
    month_match = re.match(r"^(\d{1,2})[/-](\d{4})$", text)
    if month_match:
        month = int(month_match.group(1))
        year = int(month_match.group(2))
        if 1 <= month <= 12:
            return pd.Timestamp(year=year, month=month, day=1)
    iso_month_match = re.match(r"^(\d{4})-(\d{1,2})$", text)
    if iso_month_match:
        year = int(iso_month_match.group(1))
        month = int(iso_month_match.group(2))
        if 1 <= month <= 12:
            return pd.Timestamp(year=year, month=month, day=1)
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed


def date_input_value(value):
    parsed = parse_user_date(value)
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d")


def format_short_date(value):
    parsed = parse_user_date(value)
    if parsed is None:
        return str(value or "").strip() or "Chưa cập nhật"
    return parsed.strftime("%d/%m/%Y")


def parse_date_series(values):
    parsed = pd.to_datetime(values, errors="coerce", format="%Y-%m-%d")
    missing = parsed.isna()
    if missing.any():
        fallback = pd.to_datetime(values[missing], errors="coerce", dayfirst=True)
        parsed.loc[missing] = fallback
    return parsed


def parse_due_date(value):
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return None
    parsed = parse_user_date(text)
    if parsed is None:
        return None
    return parsed.to_pydatetime()


def due_alerts(next_item):
    alerts = []
    today = now_vn().date()
    targets = [
        ("Bảo dưỡng", next_item.get("Dự kiến lần tiếp theo")),
        ("Hạn đăng kiểm", next_item.get("Hạn đăng kiểm đến")),
    ]
    for label, raw_value in targets:
        due_date = parse_due_date(raw_value)
        if not due_date:
            continue
        display_value = due_date.strftime("%d/%m/%Y")
        days = (due_date.date() - today).days
        if days < 0:
            alerts.append({"level": "danger", "text": f"{label} đã quá hạn: {display_value}"})
        elif days <= 30:
            alerts.append({"level": "warning", "text": f"{label} sắp đến hạn: {display_value}"})
    return alerts


def due_state(raw_value):
    due_date = parse_due_date(raw_value)
    if not due_date:
        return {
            "key": "missing",
            "label": "Chưa cập nhật",
            "days": None,
            "date": str(raw_value or "").strip() or "Chưa cập nhật",
        }

    today = now_vn().date()
    days = (due_date.date() - today).days
    if days < 0:
        key = "overdue"
        label = "Quá hạn"
    elif days <= 30:
        key = "due"
        label = "Sắp đến hạn"
    else:
        key = "ok"
        label = "Còn hạn"
    return {
        "key": key,
        "label": label,
        "days": days,
        "date": due_date.strftime("%d/%m/%Y"),
    }


def status_text(state):
    days = state.get("days")
    if days is None:
        return "Chưa có ngày"
    if days < 0:
        return f"Quá {abs(days)} ngày"
    if days == 0:
        return "Đến hạn hôm nay"
    return f"Còn {days} ngày"


def count_due_states(rows, field):
    counts = {"overdue": 0, "due": 0, "ok": 0, "missing": 0}
    for row in rows:
        key = row.get(field, {}).get("key", "missing")
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_status_segments(counts, total):
    items = [
        ("overdue", "Quá hạn"),
        ("due", "Sắp đến hạn"),
        ("ok", "Còn hạn"),
        ("missing", "Chưa cập nhật"),
    ]
    segments = []
    divisor = max(total, 1)
    for key, label in items:
        value = int(counts.get(key, 0))
        percent = round(value * 100 / divisor, 1) if total else 0
        segments.append({"key": key, "label": label, "value": value, "percent": percent})
    return segments


def dashboard_rows(data, plates):
    df_xe = data["xe"]
    df_next = data["next"]
    df_history = data["history"]
    labels = plate_label_map(df_xe)
    rows = []

    for plate in plates:
        car_row = df_xe[df_xe["Biển số"].astype(str) == plate]
        car = car_row.iloc[0].to_dict() if not car_row.empty else {}

        next_row = df_next[df_next["Biển số"].astype(str) == plate]
        next_item = next_row.iloc[0].to_dict() if not next_row.empty else {}

        history_count = 0
        repair_cost = 0
        periodic_cost = 0
        if not df_history.empty and "Biển số" in df_history.columns:
            history_view = df_history[df_history["Biển số"].astype(str) == plate].copy()
            history_count = len(history_view)
            if "Chi phí" in history_view.columns:
                history_view["_cost"] = pd.to_numeric(history_view["Chi phí"], errors="coerce").fillna(0)
                if "LoaiChiPhi" in history_view.columns:
                    history_view["_cost_type"] = history_view["LoaiChiPhi"].apply(normalize_history_cost_type)
                else:
                    history_view["_cost_type"] = COST_TYPE_REPAIR
                repair_cost = history_view[history_view["_cost_type"] == COST_TYPE_REPAIR]["_cost"].sum()
                periodic_cost = history_view[history_view["_cost_type"] == COST_TYPE_PERIODIC]["_cost"].sum()

        oil_cost = parse_money_value(next_item.get("Chi phí thay dầu"))
        registry_cost = parse_money_value(next_item.get("Chi phí đăng kiểm"))
        total_cost = repair_cost + periodic_cost + oil_cost + registry_cost

        maintenance = due_state(next_item.get("Dự kiến lần tiếp theo"))
        registry = due_state(next_item.get("Hạn đăng kiểm đến"))
        urgent_score = min(
            [
                value
                for value in [maintenance.get("days"), registry.get("days")]
                if value is not None
            ]
            or [9999]
        )

        rows.append(
            {
                "plate": plate,
                "label": labels.get(plate, plate),
                "car_type": car.get("Loại xe", "Chưa cập nhật"),
                "status": car.get("Trạng thái", "Chưa cập nhật"),
                "maintenance": maintenance,
                "registry": registry,
                "history_count": history_count,
                "total_cost": format_vnd(total_cost),
                "cost_breakdown": [
                    {"label": "Sửa chữa", "amount": format_vnd(repair_cost)},
                    {"label": "Bảo dưỡng định kỳ", "amount": format_vnd(periodic_cost)},
                    {"label": "Thay dầu", "amount": format_vnd(oil_cost)},
                    {"label": "Đăng kiểm", "amount": format_vnd(registry_cost)},
                ],
                "sort_score": urgent_score,
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            row["sort_score"],
            row["maintenance"].get("key") == "missing" and row["registry"].get("key") == "missing",
            row["plate"],
        ),
    )


def dashboard_cost_summary(data, plates, period="year", selected_value=None):
    df_history = data["history"]
    df_next = data["next"]
    today = now_vn()
    current_quarter = ((today.month - 1) // 3) + 1
    empty = {
        "period": "year",
        "selected": str(today.year),
        "label": f"Năm {today.year}",
        "date_label": f"01/01/{today.year} - 31/12/{today.year}",
        "total": format_vnd(0),
        "record_count": 0,
        "vehicle_count": 0,
        "average": format_vnd(0),
        "top_plate": "Chưa có dữ liệu",
        "top_amount": format_vnd(0),
        "top_rows": [],
        "top_max_height": 0,
        "categories": [
            {"key": "repair", "label": "Sửa chữa", "amount": format_vnd(0), "raw": 0},
            {"key": "periodic", "label": "Bảo dưỡng định kỳ", "amount": format_vnd(0), "raw": 0},
            {"key": "oil", "label": "Thay dầu", "amount": format_vnd(0), "raw": 0},
            {"key": "registry", "label": "Đăng kiểm", "amount": format_vnd(0), "raw": 0},
        ],
        "category_groups": [
            {"key": "cost-repair", "title": "Sửa chữa", "rows": [], "row_type": "cost", "unit": "lượt"},
            {"key": "cost-periodic", "title": "Bảo dưỡng định kỳ", "rows": [], "row_type": "cost", "unit": "lượt"},
            {"key": "cost-oil", "title": "Thay dầu", "rows": [], "row_type": "cost", "unit": "lượt"},
            {"key": "cost-registry", "title": "Đăng kiểm", "rows": [], "row_type": "cost", "unit": "lượt"},
        ],
        "month_options": [today.strftime("%Y-%m")],
        "quarter_options": [{"value": f"{today.year}-Q{current_quarter}", "label": f"Quý {current_quarter}/{today.year}"}],
        "year_options": [str(today.year)],
    }
    if not plates:
        return empty

    plate_col = "Biển số"
    events = []
    all_dates = []
    plate_type_counts = {}
    car_labels = plate_label_map(data["xe"])
    car_types = {}
    df_xe = data.get("xe")
    if df_xe is not None and not df_xe.empty and "Biển số" in df_xe.columns and "Loại xe" in df_xe.columns:
        for _, car_row in df_xe.iterrows():
            plate_key = str(car_row.get("Biển số", "")).strip()
            if plate_key:
                car_types[plate_key] = str(car_row.get("Loại xe", "")).strip()

    if not df_history.empty and all(column in df_history.columns for column in (plate_col, "Ngày", "Chi phí")):
        history_view = df_history[df_history[plate_col].astype(str).isin(plates)].copy()
        if not history_view.empty:
            history_view["_date"] = parse_date_series(history_view["Ngày"])
            history_view["_cost"] = pd.to_numeric(history_view["Chi phí"], errors="coerce").fillna(0)
            if "LoaiChiPhi" in history_view.columns:
                history_view["_cost_type"] = history_view["LoaiChiPhi"].apply(normalize_history_cost_type)
            else:
                history_view["_cost_type"] = COST_TYPE_REPAIR
            history_view = history_view.dropna(subset=["_date"])
            all_dates.extend(history_view["_date"].tolist())
            for _, row in history_view.iterrows():
                cost_type = normalize_history_cost_type(row.get("_cost_type"))
                counts = plate_type_counts.setdefault(
                    str(row[plate_col]),
                    {"repair": 0, "periodic": 0, "oil": 0, "registry": 0},
                )
                counts[cost_type] = counts.get(cost_type, 0) + 1
                events.append(
                    {
                        "plate": str(row[plate_col]),
                        "date": row["_date"],
                        "amount": float(row["_cost"]),
                        "category": "periodic" if cost_type == COST_TYPE_PERIODIC else "repair",
                        "detail": clean_history_content(row.get("Nội dung", "")) or "Lịch sử bảo dưỡng",
                        "source": "Lịch sử bảo dưỡng",
                    }
                )

    if not df_next.empty and plate_col in df_next.columns:
        next_view = df_next[df_next[plate_col].astype(str).isin(plates)].copy()
        if not next_view.empty:
            for _, row in next_view.iterrows():
                plate = str(row.get(plate_col, ""))
                registration_date = parse_user_date(row.get("Ngày đăng kiểm"))
                oil_date = registration_date
                oil_amount = parse_money_value(row.get("Chi phí thay dầu"))
                if oil_date is not None and oil_amount > 0:
                    all_dates.append(oil_date)
                    counts = plate_type_counts.setdefault(plate, {"repair": 0, "periodic": 0, "oil": 0, "registry": 0})
                    counts["oil"] = counts.get("oil", 0) + 1
                    events.append(
                        {
                            "plate": plate,
                            "date": oil_date,
                            "amount": oil_amount,
                            "category": "oil",
                            "detail": "Chi phí thay dầu",
                            "source": "Lịch bảo dưỡng tiếp theo",
                        }
                    )

                registry_date = registration_date
                registry_amount = parse_money_value(row.get("Chi phí đăng kiểm"))
                if registry_date is not None and registry_amount > 0:
                    all_dates.append(registry_date)
                    counts = plate_type_counts.setdefault(plate, {"repair": 0, "periodic": 0, "oil": 0, "registry": 0})
                    counts["registry"] = counts.get("registry", 0) + 1
                    events.append(
                        {
                            "plate": plate,
                            "date": registry_date,
                            "amount": registry_amount,
                            "category": "registry",
                            "detail": "Chi phí đăng kiểm",
                            "source": "Lịch bảo dưỡng tiếp theo",
                        }
                    )

    if not events:
        return empty

    dates = pd.to_datetime(pd.Series(all_dates), errors="coerce").dropna()
    if dates.empty:
        return empty

    month_options = sorted(dates.dt.strftime("%Y-%m").unique().tolist(), reverse=True)
    years = sorted({int(value) for value in dates.dt.year.dropna().tolist()} | {today.year}, reverse=True)
    quarter_values = sorted(
        {
            f"{int(year)}-Q{((int(month) - 1) // 3) + 1}"
            for year, month in zip(dates.dt.year, dates.dt.month)
        }
        | {f"{today.year}-Q{current_quarter}"},
        reverse=True,
    )
    quarter_options = [
        {
            "value": value,
            "label": f"Quý {value.split('-Q')[1]}/{value.split('-Q')[0]}",
        }
        for value in quarter_values
    ]

    period = period if period in ("month", "quarter", "year") else "year"
    if period == "quarter":
        selected = str(selected_value or f"{today.year}-Q{current_quarter}")
        quarter_match = re.match(r"^(\d{4})-Q([1-4])$", selected)
        if not quarter_match:
            selected = f"{today.year}-Q{current_quarter}"
            quarter_match = re.match(r"^(\d{4})-Q([1-4])$", selected)
        year = int(quarter_match.group(1))
        quarter = int(quarter_match.group(2))
        start_month = (quarter - 1) * 3 + 1
        start = datetime(year, start_month, 1)
        end = datetime(year + (1 if start_month == 10 else 0), 1 if start_month == 10 else start_month + 3, 1)
        label = f"Quý {quarter}/{year}"
    elif period == "year":
        selected = str(selected_value or today.year)
        if not re.match(r"^\d{4}$", selected):
            selected = str(today.year)
        year = int(selected)
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        label = f"Năm {year}"
    else:
        period = "month"
        selected = str(selected_value or today.strftime("%Y-%m"))
        month_match = re.match(r"^(\d{4})-(\d{2})$", selected)
        if not month_match:
            selected = today.strftime("%Y-%m")
            month_match = re.match(r"^(\d{4})-(\d{2})$", selected)
        year = int(month_match.group(1))
        month = int(month_match.group(2))
        if month < 1 or month > 12:
            year, month = today.year, today.month
            selected = today.strftime("%Y-%m")
        start = datetime(year, month, 1)
        end = datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
        label = f"Tháng {month:02d}/{year}"

    period_events = [
        event
        for event in events
        if event["date"] is not None and event["date"] >= start and event["date"] < end
    ]
    category_totals = {"repair": 0, "periodic": 0, "oil": 0, "registry": 0}
    plate_totals = {}
    plate_counts = {}
    plate_category_totals = {}
    for event in period_events:
        amount = float(event.get("amount") or 0)
        category = event.get("category")
        plate = event.get("plate", "")
        if category in category_totals:
            category_totals[category] += amount
            category_map = plate_category_totals.setdefault(plate, {"repair": 0, "periodic": 0, "oil": 0, "registry": 0})
            category_map[category] = category_map.get(category, 0) + amount
        plate_totals[plate] = plate_totals.get(plate, 0) + amount
        plate_counts[plate] = plate_counts.get(plate, 0) + 1

    total_cost = sum(category_totals.values())
    record_count = int(len(period_events))
    vehicle_count = int(len([plate for plate, amount in plate_totals.items() if amount or plate_counts.get(plate)]))
    average = total_cost / record_count if record_count else 0
    categories = [
        {"key": "repair", "label": "Sửa chữa", "amount": format_vnd(category_totals["repair"]), "raw": category_totals["repair"]},
        {"key": "periodic", "label": "Bảo dưỡng định kỳ", "amount": format_vnd(category_totals["periodic"]), "raw": category_totals["periodic"]},
        {"key": "oil", "label": "Thay dầu", "amount": format_vnd(category_totals["oil"]), "raw": category_totals["oil"]},
        {"key": "registry", "label": "Đăng kiểm", "amount": format_vnd(category_totals["registry"]), "raw": category_totals["registry"]},
    ]
    category_details = {"repair": [], "periodic": [], "oil": [], "registry": []}
    for event in sorted(period_events, key=lambda item: item["date"], reverse=True):
        key = event.get("category")
        if key not in category_details:
            continue
        plate = str(event.get("plate", ""))
        category_details[key].append(
            {
                "plate": plate,
                "label": car_labels.get(plate, plate),
                "car_type": car_types.get(plate, ""),
                "date": format_short_date(event.get("date")),
                "amount": format_vnd(event.get("amount", 0)),
                "detail": str(event.get("detail", "")).strip() or "Chưa cập nhật",
                "source": str(event.get("source", "")).strip() or "Chưa cập nhật",
            }
        )
    category_groups = [
        {"key": "cost-repair", "title": "Sửa chữa", "rows": category_details["repair"], "row_type": "cost", "unit": "lượt"},
        {"key": "cost-periodic", "title": "Bảo dưỡng định kỳ", "rows": category_details["periodic"], "row_type": "cost", "unit": "lượt"},
        {"key": "cost-oil", "title": "Thay dầu", "rows": category_details["oil"], "row_type": "cost", "unit": "lượt"},
        {"key": "cost-registry", "title": "Đăng kiểm", "rows": category_details["registry"], "row_type": "cost", "unit": "lượt"},
    ]

    top_rows = []
    top_plate = "Chưa có dữ liệu"
    top_amount = 0
    if plate_totals:
        sorted_totals = sorted(plate_totals.items(), key=lambda item: item[1], reverse=True)[:10]
        max_total = float(sorted_totals[0][1]) if sorted_totals else 0
        category_labels = {
            "repair": "Sửa chữa",
            "periodic": "Bảo dưỡng định kỳ",
            "oil": "Thay dầu",
            "registry": "Đăng kiểm",
        }
        for plate, amount in sorted_totals:
            counts = plate_type_counts.get(plate, {"repair": 0, "periodic": 0, "oil": 0, "registry": 0})
            category_amounts = plate_category_totals.get(plate, {"repair": 0, "periodic": 0, "oil": 0, "registry": 0})
            segments = []
            for key in ("repair", "periodic", "oil", "registry"):
                segment_amount = float(category_amounts.get(key) or 0)
                if segment_amount <= 0:
                    continue
                segments.append(
                    {
                        "key": key,
                        "label": category_labels[key],
                        "amount": format_vnd(segment_amount),
                        "percent": round((segment_amount / amount) * 100, 1) if amount else 0,
                    }
                )
            count_parts = []
            if counts.get("repair"):
                count_parts.append({"key": "repair", "text": f'{counts["repair"]} sửa chữa'})
            if counts.get("periodic"):
                count_parts.append({"key": "periodic", "text": f'{counts["periodic"]} định kỳ'})
            if counts.get("oil"):
                count_parts.append({"key": "oil", "text": f'{counts["oil"]} thay dầu'})
            if counts.get("registry"):
                count_parts.append({"key": "registry", "text": f'{counts["registry"]} đăng kiểm'})
            count_text = " | ".join(item["text"] for item in count_parts) if count_parts else "0 lượt"
            top_rows.append(
                {
                    "plate": plate,
                    "label": car_labels.get(plate, plate),
                    "car_type": car_types.get(plate, ""),
                    "amount": format_vnd(amount),
                    "count": int(sum(counts.values())),
                    "count_text": count_text,
                    "count_parts": count_parts,
                    "percent": round((amount / max_total) * 100, 1) if max_total else 0,
                    "segments": segments,
                }
            )
        if top_rows:
            top_plate = top_rows[0]["label"]
            top_amount = float(sorted_totals[0][1])

    return {
        "period": period,
        "selected": selected,
        "label": label,
        "date_label": f"{start.strftime('%d/%m/%Y')} - {(end - timedelta(days=1)).strftime('%d/%m/%Y')}",
        "total": format_vnd(total_cost),
        "record_count": record_count,
        "vehicle_count": vehicle_count,
        "average": format_vnd(average),
        "top_plate": top_plate,
        "top_amount": format_vnd(top_amount),
        "top_rows": top_rows,
        "categories": categories,
        "category_groups": category_groups,
        "month_options": month_options or empty["month_options"],
        "quarter_options": quarter_options or empty["quarter_options"],
        "year_options": [str(year) for year in years],
    }


def safe_unique(df, column):
    if df.empty or column not in df.columns:
        return []
    return sorted([str(v) for v in df[column].dropna().unique().tolist() if str(v).strip()])


def plate_label_map(df):
    if df.empty or "Biển số" not in df.columns:
        return {}
    labels = {}
    for _, row in df.iterrows():
        plate = str(row.get("Biển số", "")).strip()
        car_type = str(row.get("Loại xe", "")).strip()
        if not plate:
            continue
        labels[plate] = f"{plate} - {car_type}" if car_type and car_type.lower() != "nan" else plate
    return labels


def parse_plate_list(value):
    raw = str(value or "").strip()
    if not raw:
        return []
    if raw == "ALL":
        return ["ALL"]
    return [item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()]


def format_plate_list(value, labels=None):
    labels = labels or {}
    plates = parse_plate_list(value)
    if plates == ["ALL"]:
        return "Tất cả xe"
    return "\n".join(labels.get(plate, plate) for plate in plates)


def allowed_plates(data):
    info = current_access()
    selected_plates = parse_plate_list(info.get("bien_so"))
    active_plates = safe_unique(active_vehicle_df(data["xe"]), "Biển số")
    if selected_plates == ["ALL"]:
        return active_plates
    valid_plates = set(active_plates)
    return [plate for plate in selected_plates if plate in valid_plates]


def build_history_rows(df):
    if df.empty:
        return []
    view = df.copy()
    view["Ngày"] = parse_date_series(view["Ngày"])
    view = view.dropna(subset=["Ngày"])
    view["Chi phí"] = pd.to_numeric(view["Chi phí"], errors="coerce").fillna(0)
    if "LoaiChiPhi" in view.columns:
        view["LoaiChiPhi"] = view["LoaiChiPhi"].apply(normalize_history_cost_type)
    else:
        view["LoaiChiPhi"] = COST_TYPE_REPAIR
    view = view.sort_values("Ngày", ascending=False)

    rows = []
    for _, row in view.iterrows():
        rows.append(
            {
                "id": row.get("ID", ""),
                "plate": row.get("Biển số", ""),
                "date": row["Ngày"].strftime("%d/%m/%Y"),
                "date_raw": row["Ngày"].strftime("%Y-%m-%d"),
                "content": display_history_content(row.get("Nội dung", "")),
                "content_raw": str(row.get("Nội dung", "") or "").strip(),
                "cost_raw": row.get("Chi phí", 0),
                "cost_input": format_money_input(row.get("Chi phí", 0)),
                "cost": format_vnd(row.get("Chi phí", 0)),
                "cost_type": normalize_history_cost_type(row.get("LoaiChiPhi")),
                "cost_type_label": history_cost_label(row.get("LoaiChiPhi")),
            }
        )
    return rows


def build_next_service_logs(plate, limit=6):
    init_db()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                last_service,
                next_due,
                suggestion,
                registry_date,
                registry_due,
                oil_cost,
                registry_cost,
                saved_at
            FROM next_service_logs
            WHERE plate = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (normalize_plate(plate), int(limit)),
        ).fetchall()
    items = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "last_service": row["last_service"] or "Chưa cập nhật",
                "next_due": row["next_due"] or "Chưa cập nhật",
                "suggestion": row["suggestion"] or "Chưa cập nhật",
                "registry_date": row["registry_date"] or "Chưa cập nhật",
                "registry_due": row["registry_due"] or "Chưa cập nhật",
                "oil_cost": format_vnd(row["oil_cost"]),
                "registry_cost": format_vnd(row["registry_cost"]),
                "saved_at": format_datetime_display(row["saved_at"]),
                "last_service_raw": date_input_value(row["last_service"]),
                "next_due_raw": date_input_value(row["next_due"]),
                "suggestion_raw": row["suggestion"] or "",
                "registry_date_raw": date_input_value(row["registry_date"]),
                "registry_due_raw": date_input_value(row["registry_due"]),
                "oil_cost_raw": format_money_input(row["oil_cost"]),
                "registry_cost_raw": format_money_input(row["registry_cost"]),
            }
        )
    return items


def create_access_code(plates, ttl_hours=ACCESS_TTL_HOURS):
    code = gen_access_code()
    created_at = now_vn().strftime("%Y-%m-%d %H:%M")
    ttl_hours = parse_ttl_hours(ttl_hours)
    init_db()
    with db_connect() as conn:
        while conn.execute("SELECT 1 FROM access_codes WHERE code = ?", (code,)).fetchone():
            code = gen_access_code()
        conn.execute(
            """
            INSERT INTO access_codes (code, plates, created_at, ttl_hours)
            VALUES (?, ?, ?, ?)
            """,
            (code, ",".join(plates), created_at, ttl_hours),
        )
    clear_cache(SHEET_ACCESS)
    audit_log("create_access_code", f"Tạo mã {code} cho {len(plates)} xe")
    return code, created_at


def revoke_access_code(code):
    init_db()
    with db_connect() as conn:
        result = conn.execute("DELETE FROM access_codes WHERE code = ?", (code,))
    clear_cache(SHEET_ACCESS)
    if result.rowcount > 0:
        audit_log("revoke_access_code", f"Thu hồi mã {code}")
    return result.rowcount > 0


def extend_access_code(code, extra_hours):
    init_db()
    extra_hours = parse_ttl_hours(extra_hours)
    with db_connect() as conn:
        row = conn.execute(
            "SELECT created_at, ttl_hours FROM access_codes WHERE code = ?",
            (code,),
        ).fetchone()
        if not row:
            return False
        created_at = row["created_at"]
        current_ttl = parse_ttl_hours(row["ttl_hours"])
        if access_is_active(created_at, current_ttl):
            conn.execute(
                "UPDATE access_codes SET ttl_hours = ? WHERE code = ?",
                (current_ttl + extra_hours, code),
            )
        else:
            conn.execute(
                "UPDATE access_codes SET created_at = ?, ttl_hours = ? WHERE code = ?",
                (now_vn().strftime("%Y-%m-%d %H:%M"), extra_hours, code),
            )
    clear_cache(SHEET_ACCESS)
    audit_log("extend_access_code", f"Gia hạn mã {code} thêm {extra_hours} giờ")
    return True


def upsert_vehicle(plate, car_type, manufacture_year, status):
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO cars (plate, car_type, manufacture_year, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plate) DO UPDATE SET
                car_type = excluded.car_type,
                manufacture_year = excluded.manufacture_year,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                plate,
                str(car_type or "").strip(),
                str(manufacture_year or "").strip(),
                normalize_vehicle_status(status),
                now_vn().strftime("%Y-%m-%d %H:%M"),
            ),
        )
    clear_cache()
    audit_log("upsert_vehicle", f"Cập nhật xe {plate}", plate)
    return True


def deactivate_vehicle(plate):
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute(
            "UPDATE cars SET status = ?, updated_at = ? WHERE plate = ?",
            (VEHICLE_STATUS_INACTIVE, now_vn().strftime("%Y-%m-%d %H:%M"), plate),
        )
    clear_cache()
    if result.rowcount > 0:
        audit_log("deactivate_vehicle", f"Ngừng hoạt động xe {plate}", plate)
    return result.rowcount > 0


def upsert_next_service(plate, last_service, next_due, suggestion, registry_date, registry_due, oil_cost, registry_cost):
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO next_service_logs
            (plate, last_service, next_due, suggestion, registry_date, registry_due, oil_cost, registry_cost, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plate,
                str(last_service or "").strip(),
                str(next_due or "").strip(),
                str(suggestion or "").strip(),
                str(registry_date or "").strip(),
                str(registry_due or "").strip(),
                parse_money_value(oil_cost),
                parse_money_value(registry_cost),
                now_vn().strftime("%Y-%m-%d %H:%M"),
            ),
        )
        conn.execute(
            """
            INSERT INTO next_services
            (plate, last_service, next_due, suggestion, registry_due, registry_date, oil_cost, registry_cost, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plate) DO UPDATE SET
                last_service = excluded.last_service,
                next_due = excluded.next_due,
                suggestion = excluded.suggestion,
                registry_due = excluded.registry_due,
                registry_date = excluded.registry_date,
                oil_cost = excluded.oil_cost,
                registry_cost = excluded.registry_cost,
                updated_at = excluded.updated_at
            """,
            (
                plate,
                str(last_service or "").strip(),
                str(next_due or "").strip(),
                str(suggestion or "").strip(),
                str(registry_due or "").strip(),
                str(registry_date or "").strip(),
                parse_money_value(oil_cost),
                parse_money_value(registry_cost),
                now_vn().strftime("%Y-%m-%d %H:%M"),
            ),
        )
    clear_cache(SHEET_NEXT)
    audit_log("save_next_service", f"Lưu lịch bảo dưỡng/đăng kiểm cho xe {plate}", plate)
    return True


def sync_next_service_from_latest_log(plate):
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT last_service, next_due, suggestion, registry_date, registry_due, oil_cost, registry_cost, saved_at
            FROM next_service_logs
            WHERE plate = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (plate,),
        ).fetchone()
        if row:
            conn.execute(
                """
                INSERT INTO next_services
                (plate, last_service, next_due, suggestion, registry_due, registry_date, oil_cost, registry_cost, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plate) DO UPDATE SET
                    last_service = excluded.last_service,
                    next_due = excluded.next_due,
                    suggestion = excluded.suggestion,
                    registry_due = excluded.registry_due,
                    registry_date = excluded.registry_date,
                    oil_cost = excluded.oil_cost,
                    registry_cost = excluded.registry_cost,
                    updated_at = excluded.updated_at
                """,
                (
                    plate,
                    row["last_service"],
                    row["next_due"],
                    row["suggestion"],
                    row["registry_due"],
                    row["registry_date"],
                    row["oil_cost"],
                    row["registry_cost"],
                    row["saved_at"],
                ),
            )
        else:
            conn.execute("DELETE FROM next_services WHERE plate = ?", (plate,))
    clear_cache(SHEET_NEXT)
    return True


def update_next_service_log(record_id, plate, last_service, next_due, suggestion, registry_date, registry_due, oil_cost, registry_cost):
    plate = normalize_plate(plate)
    if not record_id or not plate:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute(
            """
            UPDATE next_service_logs
            SET last_service = ?, next_due = ?, suggestion = ?, registry_date = ?, registry_due = ?, oil_cost = ?, registry_cost = ?
            WHERE id = ? AND plate = ?
            """,
            (
                str(last_service or "").strip(),
                str(next_due or "").strip(),
                str(suggestion or "").strip(),
                str(registry_date or "").strip(),
                str(registry_due or "").strip(),
                parse_money_value(oil_cost),
                parse_money_value(registry_cost),
                record_id,
                plate,
            ),
        )
    if result.rowcount > 0:
        sync_next_service_from_latest_log(plate)
        audit_log("update_next_log", f"Sửa dòng lịch kế hoạch #{record_id} của xe {plate}", plate)
    return result.rowcount > 0


def delete_next_service_log(record_id, plate):
    plate = normalize_plate(plate)
    if not record_id or not plate:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute("DELETE FROM next_service_logs WHERE id = ? AND plate = ?", (record_id, plate))
    if result.rowcount > 0:
        sync_next_service_from_latest_log(plate)
        audit_log("delete_next_log", f"Xóa dòng lịch kế hoạch #{record_id} của xe {plate}", plate)
    return result.rowcount > 0


def add_history_record(plate, service_date, content, cost, cost_type=COST_TYPE_REPAIR):
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO service_history (plate, service_date, content, cost, cost_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                plate,
                str(service_date or "").strip(),
                str(content or "").strip(),
                parse_money_value(cost),
                normalize_history_cost_type(cost_type),
                now_vn().strftime("%Y-%m-%d %H:%M"),
            ),
        )
    clear_cache(SHEET_HISTORY)
    audit_log("add_history_record", f"Thêm lịch sử bảo dưỡng cho xe {plate}", plate)
    return True


def update_history_record(record_id, plate, service_date, content, cost, cost_type=COST_TYPE_REPAIR):
    plate = normalize_plate(plate)
    if not record_id or not plate:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute(
            """
            UPDATE service_history
            SET plate = ?, service_date = ?, content = ?, cost = ?, cost_type = ?
            WHERE id = ?
            """,
            (
                plate,
                str(service_date or "").strip(),
                str(content or "").strip(),
                parse_money_value(cost),
                normalize_history_cost_type(cost_type),
                record_id,
            ),
        )
    clear_cache(SHEET_HISTORY)
    if result.rowcount > 0:
        audit_log("update_history_record", f"Sửa lịch sử bảo dưỡng #{record_id} của xe {plate}", plate)
    return result.rowcount > 0


def delete_history_record(record_id):
    init_db()
    with db_connect() as conn:
        result = conn.execute("DELETE FROM service_history WHERE id = ?", (record_id,))
    clear_cache(SHEET_HISTORY)
    if result.rowcount > 0:
        audit_log("delete_history_record", f"Xóa lịch sử bảo dưỡng #{record_id}")
    return result.rowcount > 0


def delete_next_service(plate):
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute("DELETE FROM next_services WHERE plate = ?", (plate,))
    clear_cache(SHEET_NEXT)
    if result.rowcount > 0:
        audit_log("delete_next_service", f"Xóa lịch bảo dưỡng/đăng kiểm của xe {plate}", plate)
    return result.rowcount > 0


def delete_vehicle(plate):
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute("DELETE FROM cars WHERE plate = ?", (plate,))
        conn.execute("DELETE FROM next_services WHERE plate = ?", (plate,))
        conn.execute("DELETE FROM service_history WHERE plate = ?", (plate,))
        rows = conn.execute("SELECT code, plates FROM access_codes").fetchall()
        for row in rows:
            plates = [item for item in parse_plate_list(row["plates"]) if item != plate]
            if not plates:
                conn.execute("DELETE FROM access_codes WHERE code = ?", (row["code"],))
            else:
                conn.execute(
                    "UPDATE access_codes SET plates = ? WHERE code = ?",
                    (",".join(plates), row["code"]),
                )
    clear_cache()
    if result.rowcount > 0:
        audit_log("delete_vehicle", f"Xóa xe {plate}", plate)
    return result.rowcount > 0


BASE_TEMPLATE = r"""
<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title or 'Quản lý xe Vietinbank' }}</title>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" sizes="any">
  <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon-32.png') }}" sizes="32x32">
  <link rel="preload" href="{{ url_for('static', filename='fonts/SVN-Gilroy.woff2') }}" as="font" type="font/woff2">
  <link rel="preload" href="{{ url_for('static', filename='fonts/SVN-GilroyMedium.woff2') }}" as="font" type="font/woff2">
  <link rel="preload" href="{{ url_for('static', filename='fonts/SVN-GilroySemiBold.woff2') }}" as="font" type="font/woff2">
  <link rel="preload" href="{{ url_for('static', filename='fonts/SVN-GilroyBold.woff2') }}" as="font" type="font/woff2">
  <style>
    @font-face {
      font-family: "SVN-Gilroy";
      src: url("{{ url_for('static', filename='fonts/SVN-Gilroy.woff2') }}") format("woff2");
      font-weight: 400;
      font-style: normal;
      font-display: block;
    }
    @font-face {
      font-family: "SVN-Gilroy";
      src: url("{{ url_for('static', filename='fonts/SVN-GilroyMedium.woff2') }}") format("woff2");
      font-weight: 500;
      font-style: normal;
      font-display: block;
    }
    @font-face {
      font-family: "SVN-Gilroy";
      src: url("{{ url_for('static', filename='fonts/SVN-GilroySemiBold.woff2') }}") format("woff2");
      font-weight: 700;
      font-style: normal;
      font-display: block;
    }
    @font-face {
      font-family: "SVN-Gilroy";
      src: url("{{ url_for('static', filename='fonts/SVN-GilroyBold.woff2') }}") format("woff2");
      font-weight: 800;
      font-style: normal;
      font-display: block;
    }
    :root {
      color-scheme: light;
      --bg: #eef5fb;
      --panel: #ffffff;
      --ink: #09233f;
      --muted: #5d7288;
      --line: #cdddec;
      --soft: #f4f8fc;
      --brand: #005baa;
      --brand-dark: #00457f;
      --brand-soft: #e7f1fb;
      --accent: #ed1c24;
      --accent-dark: #b90f17;
      --danger: #c62828;
      --ok: #107c41;
      --shadow: 0 18px 42px rgba(0, 43, 88, .10);
    }
    * { box-sizing: border-box; }
    *, *::before, *::after {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      font-family: "SVN-Gilroy", "Roboto", Arial, Helvetica, sans-serif;
      background:
        linear-gradient(180deg, #f7fbff 0, #eef5fb 280px, #eaf1f8 100%);
      color: var(--ink);
      letter-spacing: 0;
      overflow-x: hidden;
    }
    html { overflow-x: hidden; }
    a { color: inherit; text-decoration: none; }
    .shell { min-height: 100vh; }
    .topbar {
      min-height: 74px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 30px;
      background: linear-gradient(90deg, #00457f 0%, #005baa 58%, #006bb6 100%);
      border-bottom: 4px solid var(--accent);
      position: sticky;
      top: 0;
      z-index: 10;
      box-shadow: 0 12px 30px rgba(0, 54, 108, .18);
    }
    button, input, select { font-family: inherit; }
    .brand { display: flex; align-items: center; gap: 13px; font-weight: 800; color: #fff; }
    .brand-logo {
      display: flex;
      align-items: center;
      justify-content: center;
      background: #fff;
      border-radius: 50%;
      padding: 4px;
      box-shadow: 0 10px 24px rgba(0, 23, 55, .18);
    }
    .brand-logo img {
      height: 42px;
      width: auto;
      display: block;
      border-radius: 50%;
    }
    .brand-symbol {
      width: 46px; height: 46px; border-radius: 50%;
      display: grid; place-items: center;
      background:
        radial-gradient(circle at 70% 30%, #ffffff 0 13%, transparent 14%),
        conic-gradient(from 215deg, var(--accent) 0 35%, #fff 35% 42%, #0a72bd 42% 100%);
      border: 3px solid rgba(255,255,255,.92);
      box-shadow: 0 10px 24px rgba(0, 23, 55, .22);
    }
    .brand-symbol span {
      width: 18px; height: 18px; border-radius: 50%;
      background: #005baa;
      border: 4px solid #fff;
      display: block;
    }
    .brand-text { display: grid; line-height: 1.05; }
    .brand-name {
      font-size: 26px;
      font-weight: 800;
      color: #fff;
      letter-spacing: 0;
    }
    .brand-name strong { color: #fff; }
    .brand-kicker {
      font-size: 11px;
      color: #cfe6fb;
      font-weight: 800;
      text-transform: uppercase;
    }
    .menu-toggle,
    .nav-title,
    .nav-overlay {
      display: none;
    }
    .nav { display: flex; align-items: center; gap: 8px; }
    .nav a, .btn {
      border: 1px solid rgba(255,255,255,.22);
      background: rgba(255,255,255,.10);
      border-radius: 8px;
      padding: 10px 13px;
      font-size: 14px;
      font-weight: 700;
      line-height: 1;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
    }
    .nav a {
      color: #fff;
    }
    .btn {
      border-color: #d8e2ef;
      background: #fff;
      color: var(--brand-dark);
    }
    .nav a.active {
      border-color: #fff;
      background: #fff;
      color: var(--brand-dark);
    }
    .btn.primary {
      border-color: var(--brand);
      background: linear-gradient(180deg, #0068bd, #005baa);
      color: #fff;
    }
    .btn.danger { color: var(--danger); border-color: #efc9c9; }
    .btn.full { width: 100%; }
    .nav a:hover { border-color: #fff; background: rgba(255,255,255,.18); }
    .btn:hover { border-color: #b8c7db; background: #f8fafc; color: var(--brand-dark); }
    .nav a.active:hover { background: #fff; color: var(--brand-dark); }
    .btn.primary:hover { background: linear-gradient(180deg, #005baa, #00457f); color: #fff; }
    .page {
      width: min(1220px, calc(100% - 36px));
      margin: 20px auto 38px;
    }
    .dashboard-page .page {
      margin-top: 6px;
    }
    .dashboard-page .dashboard-hero {
      display: none;
    }
    .dashboard-page .cost-overview-panel > .section-head {
      display: none;
    }
    .dashboard-page .cost-overview-panel {
      padding-top: 12px;
    }
    .login-page {
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background:
        linear-gradient(120deg, rgba(255,255,255,.08) 0 18%, transparent 18% 100%),
        linear-gradient(135deg, #003f78 0%, #005baa 48%, #0067b1 68%, #b3133b 100%);
    }
    .login-wrap {
      width: min(1060px, 100%);
      display: grid;
      grid-template-columns: 1.15fr .85fr;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .login-visual {
      background:
        linear-gradient(160deg, rgba(0,69,127,.98), rgba(0,91,170,.96) 58%, rgba(237,28,36,.88)),
        #00457f;
      color: #fff;
      padding: 40px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 460px;
    }
    .login-visual h1 { color: #fff; font-size: 32px; line-height: 1.12; font-weight: 800; white-space: nowrap; }
    .login-visual p { color: #d8ebfb; max-width: 390px; line-height: 1.55; }
    .login-stats {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
    }
    .login-stat {
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 8px;
      padding: 14px;
      background: rgba(255,255,255,.06);
    }
    .login-stat strong { display: block; font-size: 18px; margin-bottom: 4px; }
    .login-stat span { color: #c8d6e8; font-size: 13px; }
    .login-card {
      padding: 40px;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }
    .login-card h1 { margin: 0 0 8px; font-size: 26px; font-weight: 800; }
    .login-card p { margin: 0 0 22px; color: var(--muted); }
    .hero {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 12px;
    }
    .hero > .toolbar {
      flex: 0 0 calc((100% - 20px) / 3);
      width: calc((100% - 20px) / 3);
      justify-content: stretch;
    }
    .hero > .toolbar .combo {
      min-width: 0;
      width: 100%;
    }
    h1 { margin: 0; font-size: 28px; letter-spacing: 0; font-weight: 800; }
    h2 { margin: 0 0 10px; font-size: 18px; font-weight: 800; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    .subtle { color: var(--muted); font-size: 14px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .grid.single { grid-template-columns: 1fr; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
      border-top: 3px solid var(--brand);
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .section-head h2 { margin: 0; }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .toolbar form { margin: 0; }
    .toolbar select { min-width: 260px; }
    .combo {
      position: relative;
      min-width: 360px;
    }
    .combo input { padding-right: 42px; }
    .combo-toggle {
      position: absolute;
      top: 1px;
      right: 1px;
      width: 40px;
      height: 42px;
      border: 0;
      border-left: 1px solid var(--line);
      background: #fff;
      color: var(--brand-dark);
      border-radius: 0 8px 8px 0;
      cursor: pointer;
      font-size: 13px;
      font-weight: 800;
    }
    .combo-menu {
      display: none;
      position: absolute;
      z-index: 30;
      top: calc(100% + 4px);
      left: 0;
      right: 0;
      max-height: 340px;
      overflow-y: auto;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 16px 36px rgba(0, 43, 88, .16);
      padding: 6px;
    }
    .combo.open .combo-menu { display: grid; gap: 3px; }
    .combo-option {
      width: 100%;
      border: 0;
      background: #fff;
      color: var(--ink);
      text-align: left;
      padding: 9px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
      min-height: 36px;
    }
    .combo-option:hover, .combo-option.active {
      background: var(--brand-soft);
      color: var(--brand-dark);
    }
    .combo-option.selected {
      background: #e8f3fb;
      color: var(--brand-dark);
      font-weight: 800;
    }
    .selected-plates {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .selected-chip {
      border-radius: 999px;
      background: var(--brand-soft);
      color: var(--brand-dark);
      padding: 5px 9px;
      font-size: 12px;
      font-weight: 700;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .chip-remove {
      border: 0;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: rgba(0,91,170,.14);
      color: var(--brand-dark);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      font-weight: 800;
      line-height: 1;
      padding: 0;
    }
    .chip-remove:hover {
      background: var(--danger);
      color: #fff;
    }
    .form-row {
      display: grid;
      grid-template-columns: 1fr 112px;
      gap: 10px;
      align-items: end;
    }
    .field { display: grid; gap: 7px; margin-bottom: 16px; min-width: 0; }
    .field .combo { min-width: 0; width: 100%; }
    label { color: var(--muted); font-size: 13px; font-weight: 700; }
    input, select, textarea {
      width: 100%;
      max-width: 100%;
      min-width: 0;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 8px;
      min-height: 44px;
      padding: 10px 12px;
      font-size: 15px;
      outline: none;
    }
    textarea { min-height: 118px; resize: vertical; line-height: 1.45; }
    input:focus, select:focus, textarea:focus { border-color: var(--brand); box-shadow: 0 0 0 3px rgba(0, 91, 170, .14); }
    .metric-strip {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .metric-card {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 12px 16px;
    }
    .metric-card span { display: block; color: var(--muted); font-size: 12px; font-weight: 700; margin-bottom: 5px; }
    .metric-card strong { font-size: 20px; color: var(--ink); }
    .metric-card.accent { border-left: 4px solid var(--accent); }
    .metric-card.success { border-left: 4px solid #159957; }
    .metric-card.danger { border-left: 4px solid var(--danger); }
    .metric-card.brand { border-left: 4px solid var(--brand); display: block; color: var(--ink); }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 0 10px;
      border-radius: 999px;
      background: var(--brand-soft);
      color: var(--brand-dark);
      font-size: 13px;
      font-weight: 700;
    }
    .facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .fact {
      border: 1px solid var(--line);
      background: #f8fafc;
      border-radius: 10px;
      padding: 10px;
      min-height: 62px;
    }
    .fact span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 6px; }
    .fact strong { font-size: 16px; }
    .lookup-overview {
      display: grid;
      grid-template-columns: minmax(260px, .72fr) minmax(0, 1.28fr);
      gap: 14px;
      align-items: stretch;
    }
    .vehicle-compact {
      display: grid;
      gap: 10px;
      align-content: start;
    }
    .vehicle-identity {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
    }
    .vehicle-identity strong { display: block; font-size: 22px; line-height: 1.15; }
    .vehicle-identity span { color: var(--muted); font-size: 13px; font-weight: 700; }
    .vehicle-compact-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .vehicle-mini {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfdff;
    }
    .vehicle-mini span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .vehicle-mini strong { display: block; font-size: 15px; line-height: 1.25; overflow-wrap: anywhere; }
    .plan-board {
      display: grid;
      gap: 12px;
    }
    .plan-timeline {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .plan-step {
      border: 1px solid var(--line);
      border-left: 4px solid #0b75bb;
      border-radius: 8px;
      padding: 10px;
      background: #fbfdff;
      min-height: 82px;
    }
    .plan-step.periodic { border-left-color: #15965f; }
    .plan-step.registry { border-left-color: #0b75bb; }
    .plan-step.warning { border-left-color: #d88900; }
    .plan-step.danger { border-left-color: #e0194d; }
    .plan-step span { display: block; color: var(--muted); font-size: 12px; font-weight: 700; margin-bottom: 5px; }
    .plan-step strong { display: block; font-size: 16px; line-height: 1.2; overflow-wrap: anywhere; }
    .plan-step small { display: block; margin-top: 6px; color: var(--muted); font-size: 12px; font-weight: 700; }
    .plan-detail-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(190px, .45fr);
      gap: 10px;
    }
    .plan-note,
    .plan-costs {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
    }
    .plan-note span,
    .plan-cost span { display: block; color: var(--muted); font-size: 12px; font-weight: 700; margin-bottom: 5px; }
    .plan-note strong { display: block; line-height: 1.35; overflow-wrap: anywhere; }
    .plan-costs { display: grid; grid-template-columns: 1fr; gap: 8px; }
    .plan-cost {
      border-left: 4px solid #d88900;
      padding-left: 9px;
    }
    .plan-cost.registry { border-left-color: #0b75bb; }
    .plan-cost strong { display: block; font-size: 16px; }
    .schedule-log-box {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      display: grid;
      gap: 10px;
    }
    .schedule-log-list { display: grid; gap: 8px; }
    .schedule-log-row {
      display: grid;
      grid-template-columns: minmax(150px, 1fr) minmax(110px, .45fr) minmax(120px, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfdff;
    }
    .schedule-log-row strong { display: block; font-size: 13px; line-height: 1.25; }
    .schedule-log-row span, .schedule-log-note { display: block; color: var(--muted); font-size: 12px; line-height: 1.35; }
    .schedule-log-actions {
      display: flex;
      gap: 6px;
      justify-content: flex-end;
      align-items: center;
      flex-wrap: wrap;
    }
    .schedule-log-actions form { margin: 0; }
    .schedule-log-actions .btn { min-height: 32px; padding: 7px 10px; font-size: 12px; }
    .next-list { display: grid; gap: 10px; }
    .next-row {
      display: grid;
      grid-template-columns: 150px 1fr;
      gap: 12px;
      padding: 8px 0;
      border-bottom: 1px solid var(--line);
    }
    .next-row:last-child { border-bottom: 0; }
    .next-row span { color: var(--muted); }
    .alerts {
      display: grid;
      gap: 8px;
      margin-bottom: 12px;
    }
    .alert {
      border-radius: 8px;
      padding: 10px 12px;
      font-weight: 700;
      border: 1px solid;
    }
    .alert.warning {
      background: #fff7df;
      color: #946200;
      border-color: #f2d27a;
    }
    .alert.danger {
      background: #ffe5e9;
      color: #b3133b;
      border-color: #f0a8b7;
    }
    .history-filter {
      display: grid;
      grid-template-columns: 1.3fr repeat(2, 160px) auto auto;
      gap: 8px;
      align-items: end;
      margin-bottom: 12px;
    }
    .history-filter .field {
      margin-bottom: 0;
    }
    .table-wrap {
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow-x: auto;
      background: #fff;
      -webkit-overflow-scrolling: touch;
    }
    table { width: 100%; border-collapse: collapse; }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 13px 14px;
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }
    th {
      color: var(--muted);
      background: #f8fafc;
      font-size: 12px;
      text-transform: uppercase;
    }
    .history-content {
      white-space: pre-line;
      line-height: 1.55;
    }
    .cost-type-pill {
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 4px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }
    .cost-type-pill.repair { background: #ffe5e9; color: #b3133b; }
    .cost-type-pill.periodic { background: #dff7e9; color: #0b6f3d; }
    .plate-list-cell {
      white-space: pre-line;
      line-height: 1.45;
    }
    tr.access-active td { background: #f6fffa; }
    tr.access-expired td { background: #fff6f8; color: #4f5965; }
    tr.access-active td:first-child {
      border-left: 5px solid #159957;
    }
    tr.access-expired td:first-child {
      border-left: 5px solid #d91f3f;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }
    .status-pill.active { background: #159957; color: #fff; }
    .status-pill.expired { background: #d91f3f; color: #fff; }
    .access-remaining-content {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .access-remaining-content .status-pill {
      flex: 0 0 auto;
    }
    .access-remaining-text {
      color: var(--ink);
      font-weight: 600;
      white-space: nowrap;
    }
    .row-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 230px;
    }
    .access-actions {
      display: grid;
      grid-template-columns: minmax(90px, .8fr) minmax(96px, 1fr) minmax(96px, 1fr);
      align-items: stretch;
      gap: 8px;
      min-width: 300px;
    }
    .access-actions form {
      margin: 0;
    }
    .access-actions .access-extend-form,
    .access-actions .access-revoke-form {
      display: contents;
    }
    .extend-hours {
      display: grid;
      grid-template-columns: minmax(44px, 1fr) auto;
      align-items: center;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }
    .extend-hours input {
      width: 100%;
      min-height: 38px;
      border: 0;
      padding: 8px 8px;
      font-size: 14px;
    }
    .extend-hours span {
      height: 100%;
      display: inline-flex;
      align-items: center;
      padding: 0 10px;
      background: var(--brand-soft);
      color: var(--brand-dark);
      border-left: 1px solid var(--line);
      font-size: 13px;
      font-weight: 800;
      white-space: nowrap;
    }
    .access-actions .btn {
      width: 100%;
      min-height: 40px;
    }
    .admin-management-panel {
      min-height: 360px;
    }
    .admin-management-panel .section-head {
      align-items: center;
      gap: 12px;
      padding-bottom: 2px;
    }
    .admin-management-panel .table-wrap {
      border-color: #cfe0ef;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .7);
    }
    .admin-management-panel table {
      min-width: 980px;
    }
    .admin-management-panel th {
      letter-spacing: 0;
      color: #5b6b7c;
      background: #f5f9fd;
    }
    .admin-management-panel td {
      font-size: 14px;
      line-height: 1.45;
    }
    .access-code-list {
      display: grid;
      gap: 12px;
    }
    .access-code-card {
      display: grid;
      grid-template-columns: 170px minmax(220px, 1fr) minmax(170px, .65fr);
      gap: 14px;
      align-items: center;
      border: 1px solid #cfe0ef;
      border-left: 5px solid #159957;
      border-radius: 8px;
      background: linear-gradient(135deg, #ffffff 0%, #f8fcff 100%);
      padding: 14px;
      box-shadow: 0 10px 22px rgba(0, 43, 88, .06);
    }
    .access-code-card.expired {
      border-left-color: #d91f3f;
      background: linear-gradient(135deg, #fff 0%, #fff7f8 100%);
    }
    .access-code-main,
    .access-code-meta,
    .access-code-status {
      display: grid;
      gap: 5px;
      min-width: 0;
    }
    .access-code-label {
      color: var(--muted);
      font-size: 11px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .access-code-value {
      color: var(--ink);
      font-size: 15px;
      font-weight: 900;
      overflow-wrap: anywhere;
    }
    .access-copy-code {
      display: inline-flex;
      align-items: center;
      justify-content: flex-start;
      gap: 8px;
      width: fit-content;
      max-width: 100%;
      border: 0;
      background: transparent;
      padding: 0;
      font: inherit;
      color: inherit;
      cursor: pointer;
      text-align: left;
    }
    .access-copy-code:hover .code,
    .access-copy-code.copied .code {
      color: var(--brand);
      text-decoration: underline;
      text-underline-offset: 3px;
    }
    .access-copy-hint {
      border-radius: 999px;
      background: #eef6ff;
      color: var(--brand-dark);
      padding: 3px 7px;
      font-size: 11px;
      font-weight: 900;
      white-space: nowrap;
    }
    .access-copy-code.copied .access-copy-hint {
      background: #dff7e9;
      color: #0b6f3d;
    }
    .access-code-main .code {
      font-size: 18px;
      letter-spacing: .02em;
    }
    .access-code-plate {
      white-space: pre-line;
      line-height: 1.35;
      font-weight: 800;
    }
    .access-card-actions {
      grid-column: 1 / -1;
      justify-self: end;
      width: min(360px, 100%);
      display: grid;
      grid-template-columns: minmax(120px, .8fr) minmax(90px, 1fr) minmax(88px, .9fr);
      gap: 8px;
      align-items: stretch;
      padding-top: 4px;
    }
    .access-card-actions form {
      margin: 0;
    }
    .access-card-actions .access-extend-form,
    .access-card-actions .access-revoke-form {
      display: contents;
    }
    .access-card-actions .btn {
      width: 100%;
      min-height: 40px;
    }
    .audit-log-list {
      display: grid;
      gap: 12px;
    }
    .audit-log-card {
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr) 160px;
      gap: 14px;
      align-items: start;
      border: 1px solid #cfe0ef;
      border-left: 5px solid var(--brand);
      border-radius: 8px;
      background: linear-gradient(135deg, #ffffff 0%, #f8fcff 100%);
      padding: 14px;
      box-shadow: 0 10px 22px rgba(0, 43, 88, .06);
    }
    .audit-log-time,
    .audit-log-body,
    .audit-log-meta {
      display: grid;
      gap: 6px;
      min-width: 0;
    }
    .audit-log-label {
      color: var(--muted);
      font-size: 11px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .audit-log-time strong,
    .audit-log-body strong {
      color: var(--ink);
      font-weight: 900;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }
    .audit-log-detail {
      color: #4f5965;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }
    .audit-log-meta .badge {
      justify-self: start;
    }
    .audit-log-ip {
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      overflow-wrap: anywhere;
    }
    .history-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
      min-width: 120px;
    }
    .history-actions form { margin: 0; }
    .history-actions .btn {
      min-height: 32px;
      padding: 7px 9px;
      font-size: 13px;
    }
    .extend-form {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .extend-form input {
      width: 58px;
      min-height: 34px;
      padding: 7px 8px;
      font-size: 13px;
    }
    .extend-form .btn,
    .row-actions .btn {
      min-height: 34px;
      padding: 8px 10px;
      font-size: 13px;
    }
    tr:last-child td { border-bottom: 0; }
    tbody tr:hover { background: #fbfdff; }
    .cost { white-space: nowrap; font-weight: 700; }
    .messages { display: grid; gap: 8px; margin-bottom: 16px; }
    .message {
      padding: 11px 13px;
      border-radius: 8px;
      background: #edf8f1;
      color: var(--ok);
      border: 1px solid #c9ead5;
    }
    .message.error {
      background: #fff0f0;
      color: var(--danger);
      border-color: #efc9c9;
    }
    .empty {
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 10px;
      padding: 18px;
      background: #fbfcfe;
    }
    .dashboard-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(360px, .8fr);
      gap: 12px;
      margin-bottom: 12px;
    }
    .dashboard-stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .dashboard-stat {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 13px 15px;
      border-left: 4px solid var(--brand);
      font: inherit;
      text-align: left;
    }
    .dashboard-stat.danger { border-left-color: var(--danger); }
    .dashboard-stat.warning { border-left-color: #d88900; }
    .dashboard-stat.ok { border-left-color: var(--ok); }
    .dashboard-stat.money { border-left-color: #2563eb; }
    .dashboard-stat span { display: block; color: var(--muted); font-size: 12px; font-weight: 700; margin-bottom: 6px; }
    .dashboard-stat strong { display: block; font-size: 24px; line-height: 1.15; overflow-wrap: anywhere; }
    .dashboard-stat-link {
      color: inherit;
      text-decoration: none;
      display: inline-block;
    }
    .dashboard-stat-link:hover { text-decoration: underline; }
    .dashboard-stat-linkcard {
      color: inherit;
      text-decoration: none;
      cursor: pointer;
      transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease;
    }
    .dashboard-stat-linkcard:hover {
      transform: translateY(-1px);
      box-shadow: 0 12px 26px rgba(11, 48, 84, .14);
      border-color: #b9d2e8;
    }
    .dashboard-modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 80;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 18px;
      background: rgba(9, 35, 63, .42);
    }
    .dashboard-modal-backdrop.open { display: flex; }
    .dashboard-modal {
      width: min(720px, 100%);
      max-height: min(760px, calc(100vh - 36px));
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 24px 70px rgba(9, 35, 63, .28);
      overflow: hidden;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
    }
    .dashboard-modal-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
    }
    .dashboard-modal-head h3 { margin: 0; font-size: 18px; }
    .dashboard-modal-close {
      border: 1px solid var(--line);
      border-radius: 8px;
      width: 34px;
      height: 34px;
      background: #fff;
      color: var(--ink);
      font-size: 22px;
      line-height: 1;
      cursor: pointer;
    }
    .dashboard-modal-body {
      padding: 12px;
      overflow-y: auto;
    }
    .dashboard-modal-panel[hidden] { display: none; }
    .dashboard-modal-list {
      display: grid;
      gap: 8px;
    }
    .dashboard-modal-row {
      display: grid;
      grid-template-columns: minmax(160px, 1fr) minmax(160px, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 11px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: inherit;
      text-decoration: none;
      background: #fbfdff;
    }
    .dashboard-modal-row:hover { border-color: #b9d2e8; background: #f3f8fd; }
    .dashboard-modal-main strong { display: block; }
    .dashboard-modal-main span,
    .dashboard-modal-status,
    .dashboard-modal-meta { color: var(--muted); font-size: 12px; font-weight: 700; }
    .dashboard-modal-status { display: grid; gap: 4px; }
    .inline-modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 45;
      background: rgba(8, 24, 42, .42);
      display: grid;
      place-items: center;
      padding: 16px;
    }
    .inline-modal-backdrop[hidden] { display: none !important; }
    .inline-modal {
      width: min(720px, 100%);
      max-height: min(86vh, 860px);
      overflow: hidden;
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 24px 70px rgba(9, 35, 63, .3);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
    }
    .inline-modal-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
    }
    .inline-modal-body {
      padding: 16px 18px 18px;
      overflow-y: auto;
    }
    .inline-modal-grid {
      display: grid;
      gap: 10px;
    }
    .inline-modal .form-row {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .inline-modal-actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
      flex-wrap: wrap;
      margin-top: 8px;
    }
    .cost-panel {
      display: grid;
      grid-template-columns: minmax(260px, .75fr) minmax(0, 1fr);
      gap: 16px;
      align-items: stretch;
      margin-bottom: 12px;
    }
    .cost-summary {
      border: 1px solid #bfd7ef;
      background: linear-gradient(135deg, #ffffff 0%, #eef7ff 100%);
      border-radius: 8px;
      padding: 18px;
      display: grid;
      gap: 12px;
    }
    .cost-summary-chart {
      align-content: start;
    }
    .cost-total-card {
      display: grid;
      gap: 2px;
    }
    .cost-total-card span {
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
    }
    .cost-total-card strong {
      color: var(--brand-dark);
      font-size: clamp(26px, 3.2vw, 42px);
      line-height: 1.05;
      font-weight: 900;
    }
    .cost-total-card small {
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }
    .cost-category-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .cost-category-card {
      display: grid;
      gap: 4px;
      min-height: 66px;
      border: 1px solid var(--line);
      border-left: 4px solid var(--line);
      background: #fff;
      border-radius: 8px;
      padding: 10px 12px;
      appearance: none;
      text-align: left;
      font: inherit;
      color: inherit;
      cursor: pointer;
    }
    .cost-category-card:hover {
      background: #f7fbff;
      border-color: #b9d2e8;
      border-left-color: var(--category-color, #9aa9b8);
    }
    .cost-category-card.repair { --category-color: #d91f3f; border-left-color: #d91f3f; }
    .cost-category-card.periodic { --category-color: #159957; border-left-color: #159957; }
    .cost-category-card.oil { --category-color: #d88900; border-left-color: #d88900; }
    .cost-category-card.registry { --category-color: #0b75bb; border-left-color: #0b75bb; }
    .cost-category-card span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
    }
    .cost-category-card strong {
      color: var(--ink);
      font-size: 16px;
      line-height: 1.2;
      font-weight: 900;
    }
    .cost-pie-mini-panel {
      display: grid;
      grid-template-columns: 132px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, .72);
      border-radius: 8px;
      padding: 10px;
    }
    .cost-pie-legend {
      display: grid;
      grid-template-columns: 1fr;
      gap: 6px;
    }
    .cost-pie-legend-item {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;
      border: 0;
      background: transparent;
      border-radius: 6px;
      padding: 4px 6px;
      appearance: none;
      text-align: left;
      font: inherit;
      color: inherit;
      cursor: pointer;
    }
    .cost-pie-legend-item:hover {
      background: #f3f8fd;
    }
    .cost-pie-swatch {
      width: 12px;
      height: 12px;
      border-radius: 999px;
      flex: 0 0 auto;
    }
    .cost-pie-legend-item strong {
      display: block;
      font-size: 13px;
      line-height: 1.15;
    }
    .cost-pie-legend-text span {
      display: block;
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .cost-pie-percent {
      margin-left: auto;
      min-width: 46px;
      border-radius: 999px;
      background: #eef6ff;
      color: var(--brand-dark);
      padding: 4px 7px;
      text-align: center;
      font-size: 12px;
      font-weight: 900;
      white-space: nowrap;
    }
    .cost-pie-wrap {
      display: flex;
      justify-content: center;
    }
    .cost-pie {
      width: 132px;
      aspect-ratio: 1;
      border-radius: 50%;
      border: 1px solid #bfd7ef;
      box-shadow: inset 0 0 0 8px rgba(255, 255, 255, .72);
      position: relative;
    }
    .cost-pie::after {
      content: "";
      position: absolute;
      inset: 34%;
      background: #fff;
      border-radius: 50%;
      box-shadow: 0 0 0 1px rgba(9, 35, 63, .04);
    }
    .cost-pie-center {
      position: absolute;
      inset: 50% auto auto 50%;
      transform: translate(-50%, -50%);
      z-index: 1;
      text-align: center;
      width: 64%;
    }
    .cost-pie-center span {
      display: block;
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      margin-bottom: 4px;
    }
    .cost-pie-center strong {
      display: block;
      font-size: 13px;
      line-height: 1.15;
      font-weight: 900;
      color: var(--brand-dark);
      overflow-wrap: anywhere;
    }
    .cost-pie-center small {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
    }
    .cost-total-label { color: var(--muted); font-size: 13px; font-weight: 800; }
    .cost-total {
      color: var(--brand-dark);
      font-size: clamp(28px, 4vw, 42px);
      line-height: 1.05;
      font-weight: 900;
      overflow-wrap: anywhere;
    }
    .cost-period-label { color: var(--muted); font-size: 13px; }
    .cost-breakdown {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .cost-breakdown.meta {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .cost-mini {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 8px;
      padding: 10px;
    }
    .cost-mini-button {
      width: 100%;
      text-align: left;
      cursor: pointer;
      appearance: none;
    }
    .cost-mini-button:hover {
      background: #f7fbff;
      border-color: #b9d2e8;
    }
    .cost-mini.repair { border-left: 4px solid #d91f3f; }
    .cost-mini.periodic { border-left: 4px solid #159957; }
    .cost-mini.oil { border-left: 4px solid #d88900; }
    .cost-mini.registry { border-left: 4px solid #0b75bb; }
    .cost-mini span { display: block; color: var(--muted); font-size: 12px; font-weight: 700; margin-bottom: 5px; }
    .cost-mini strong { display: block; font-size: 17px; line-height: 1.2; overflow-wrap: anywhere; }
    .cost-cell-breakdown {
      display: grid;
      gap: 3px;
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      line-height: 1.35;
    }
    .cost-controls {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .cost-tabs {
      display: inline-flex;
      gap: 4px;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f6f9fc;
    }
    .cost-tab {
      border: 0;
      border-radius: 6px;
      padding: 8px 12px;
      color: var(--muted);
      font-weight: 800;
      background: transparent;
      cursor: pointer;
    }
    .cost-tab.active { background: var(--brand); color: #fff; }
    .cost-select {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 8px 10px;
      font: inherit;
      font-weight: 700;
      color: var(--ink);
    }
    .cost-chart {
      display: grid;
      gap: 9px;
      max-height: 420px;
      overflow-y: auto;
      padding-right: 4px;
    }
    .cost-row {
      display: grid;
      grid-template-columns: minmax(120px, 1fr) minmax(120px, 1.3fr) auto;
      gap: 10px;
      align-items: center;
      padding: 7px 8px;
      margin: -7px -8px;
      border-radius: 8px;
      color: inherit;
      text-decoration: none;
      transition: background .16s ease, box-shadow .16s ease;
    }
    .cost-row:hover {
      background: #f3f8fd;
      box-shadow: inset 0 0 0 1px #d7e7f5;
    }
    .cost-row-label { min-width: 0; }
    .cost-row-label strong {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .cost-row-label span { color: var(--muted); font-size: 12px; }
    .cost-row-label .count-detail {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 4px;
      margin-top: 3px;
      font-size: 12px;
      font-weight: 600;
    }
    .count-part {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 1px 6px;
      line-height: 1.45;
      background: #edf4fb;
      color: var(--muted);
    }
    .count-part.repair { background: #fde8ee; color: #b0153f; }
    .count-part.periodic { background: #e4f6ee; color: #11734a; }
    .count-part.oil { background: #fff3d9; color: #9a6400; }
    .count-part.registry { background: #e5f2fb; color: #075f99; }
    .cost-row-track {
      height: 12px;
      border-radius: 999px;
      background: #e7eef6;
      overflow: hidden;
    }
    .cost-row-fill {
      display: flex;
      height: 100%;
      border-radius: inherit;
      overflow: hidden;
      background: #0b75bb;
    }
    .cost-row-segment {
      display: block;
      height: 100%;
      min-width: 3px;
    }
    .cost-row-segment.repair { background: #e0194d; }
    .cost-row-segment.periodic { background: #15965f; }
    .cost-row-segment.oil { background: #d88900; }
    .cost-row-segment.registry { background: #0b75bb; }
    .cost-row-segment:hover { filter: brightness(1.08); }
    .cost-row-amount { font-weight: 900; white-space: nowrap; }
    .dashboard-list-controls {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      margin: 0 0 12px;
    }
    .dashboard-list-search {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      min-width: 0;
    }
    .dashboard-list-search .vehicle-search {
      width: min(360px, 48vw);
      min-width: 240px;
    }
    .dashboard-list-size {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
    }
    .dashboard-list-size label { white-space: nowrap; }
    .dashboard-list-size .cost-select { min-width: 96px; }
    .chart-stack { display: grid; gap: 16px; }
    .chart-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 9px;
    }
    .chart-bar {
      height: 18px;
      border-radius: 999px;
      overflow: hidden;
      background: #e7eef6;
      display: flex;
      margin-bottom: 10px;
    }
    .chart-segment { min-width: 3px; }
    .chart-segment.overdue { background: var(--danger); }
    .chart-segment.due { background: #d88900; }
    .chart-segment.ok { background: var(--ok); }
    .chart-segment.missing { background: #93a4b7; }
    .chart-legend {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .legend-item {
      display: inline-flex;
      align-items: center;
      justify-content: flex-start;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
    }
    .legend-label { display: inline-flex; align-items: center; gap: 7px; }
    .legend-value { color: var(--ink); font-weight: 800; }
    .legend-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }
    .legend-dot.overdue { background: var(--danger); }
    .legend-dot.due { background: #d88900; }
    .legend-dot.ok { background: var(--ok); }
    .legend-dot.missing { background: #93a4b7; }
    .vehicle-list {
      display: grid;
      gap: 8px;
      max-height: 430px;
      overflow: auto;
      padding-right: 2px;
    }
    .vehicle-card {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 8px;
      padding: 11px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: start;
    }
    .vehicle-card strong { display: block; font-size: 16px; margin-bottom: 4px; }
    .vehicle-meta { color: var(--muted); font-size: 12px; line-height: 1.35; }
    .vehicle-statuses {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      grid-column: 1 / -1;
    }
    .due-pill {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      border-radius: 999px;
      padding: 6px 9px;
      font-size: 12px;
      font-weight: 800;
      background: var(--brand-soft);
      color: var(--brand-dark);
      white-space: nowrap;
    }
    .due-pill.overdue { background: #ffe5e9; color: #b3133b; }
    .due-pill.due { background: #fff2cf; color: #946200; }
    .due-pill.ok { background: #dff7e9; color: #107c41; }
    .due-pill.missing { background: #eef2f6; color: #637386; }
    .admin-layout { display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }
    .admin-layout.no-form { grid-template-columns: 1fr; }
    .admin-shell { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 18px; align-items: start; }
    .admin-sidebar { position: sticky; top: 92px; display: grid; gap: 8px; }
    .admin-menu { display: grid; gap: 8px; }
    .admin-menu a {
      display: block;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 13px;
      background: #fff;
      color: var(--ink);
      text-decoration: none;
      font-weight: 800;
    }
    .admin-menu a span { display: block; margin-top: 3px; color: var(--muted); font-size: 12px; font-weight: 600; line-height: 1.35; }
    .admin-menu a.active {
      border-color: var(--brand);
      background: var(--brand-soft);
      color: var(--brand-dark);
      box-shadow: 0 8px 18px rgba(0, 75, 141, .12);
    }
    .admin-content { min-width: 0; }
    .vehicle-search {
      flex: 1 1 280px;
      min-width: 240px;
    }
    .vehicle-search input {
      min-height: 38px;
    }
    .vehicle-list-tools {
      flex: 1 1 420px;
      justify-content: flex-end;
      flex-wrap: nowrap;
      min-width: 0;
    }
    .vehicle-list-tools .vehicle-search {
      flex: 0 1 360px;
      min-width: 220px;
    }
    .vehicle-list-tools .badge {
      flex: 0 0 auto;
      white-space: nowrap;
    }
    .vehicle-controls {
      display: grid;
      grid-template-columns: minmax(260px, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      margin: 0 0 12px;
    }
    .vehicle-controls form {
      margin: 0;
    }
    .vehicle-controls .vehicle-search {
      min-width: 0;
    }
    .vehicle-controls .badge,
    .vehicle-controls .btn {
      white-space: nowrap;
    }
    .vehicle-controls .btn {
      width: auto;
    }
    .vehicle-table-action {
      display: flex;
      justify-content: flex-start;
      margin: 0 0 10px;
    }
    .vehicle-table-action .btn {
      flex: 0 0 auto;
    }
    .admin-content .form-row {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .admin-content input[type="date"] {
      min-width: 0;
      padding-right: 9px;
      font-size: 14px;
    }
    .maintenance-card {
      display: flex;
      flex-direction: column;
    }
    .maintenance-form {
      display: flex;
      flex: 1;
      flex-direction: column;
    }
    .maintenance-form .btn.full {
      margin-top: auto;
    }
    .vehicle-modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 80;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 74px 18px 24px;
      background: rgba(15, 23, 42, .48);
      overflow-y: auto;
    }
    .vehicle-modal {
      width: min(560px, 100%);
      box-shadow: 0 24px 70px rgba(15, 23, 42, .28);
    }
    .vehicle-modal-close {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 36px;
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      text-decoration: none;
      font-size: 24px;
      line-height: 1;
      font-weight: 700;
    }
    .vehicle-modal-actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 12px;
    }
    .pagination {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-top: 12px;
      flex-wrap: wrap;
    }
    .pagination-pages {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .pagination .btn {
      background: #fff;
      border-color: var(--line);
      color: var(--text);
      min-height: 34px;
      padding: 8px 11px;
      font-size: 13px;
    }
    .pagination .btn:hover {
      background: #f5f8fb;
      color: var(--text);
    }
    .pagination .btn.active {
      background: var(--brand);
      color: #fff;
      border-color: var(--brand);
    }
    .pagination .btn.active:hover {
      background: var(--brand-dark);
      color: #fff;
    }
    .pagination .btn.disabled {
      opacity: .45;
      pointer-events: none;
    }
    .admin-vehicle-picker { margin-bottom: 12px; }
    .code { font-family: inherit; font-weight: 700; }
    @media (max-width: 820px) {
      .topbar { height: auto; padding: 14px 16px; align-items: center; gap: 12px; }
      .menu-toggle {
        width: 44px;
        height: 44px;
        border: 1px solid rgba(255,255,255,.28);
        border-radius: 8px;
        background: rgba(255,255,255,.12);
        color: #fff;
        display: inline-grid;
        place-items: center;
        gap: 0;
        flex: 0 0 auto;
        cursor: pointer;
      }
      .menu-toggle span {
        width: 20px;
        height: 2px;
        background: currentColor;
        border-radius: 999px;
        display: block;
        box-shadow: 0 -7px 0 currentColor, 0 7px 0 currentColor;
      }
      .nav-overlay {
        position: fixed;
        inset: 0;
        z-index: 39;
        background: rgba(5, 20, 38, .42);
        opacity: 0;
        pointer-events: none;
        transition: opacity .18s ease;
      }
      .nav {
        position: fixed;
        top: 0;
        left: 0;
        bottom: 0;
        z-index: 40;
        width: min(290px, 84vw);
        padding: 18px 14px;
        display: flex;
        align-items: stretch;
        flex-direction: column;
        gap: 8px;
        background: linear-gradient(180deg, #00457f 0%, #005baa 62%, #006bb6 100%);
        border-right: 4px solid var(--accent);
        box-shadow: 18px 0 40px rgba(0, 31, 67, .28);
        transform: translateX(-104%);
        transition: transform .2s ease;
      }
      .nav-title {
        display: block;
        color: #cfe6fb;
        font-size: 12px;
        font-weight: 800;
        text-transform: uppercase;
        padding: 6px 4px 10px;
      }
      .nav a {
        width: 100%;
        min-height: 46px;
        justify-content: flex-start;
        padding: 13px 14px;
        background: rgba(255,255,255,.08);
      }
      body.menu-open {
        overflow: hidden;
      }
      body.menu-open .nav {
        transform: translateX(0);
      }
      body.menu-open .nav-overlay {
        display: block;
        opacity: 1;
        pointer-events: auto;
      }
      .hero { align-items: flex-start; flex-direction: column; }
      .hero > .toolbar { width: 100%; flex: 0 0 auto; }
      .history-filter { grid-template-columns: 1fr; }
      .grid, .admin-layout, .admin-shell, .metric-strip, .login-wrap, .dashboard-grid, .dashboard-stats, .cost-panel, .cost-breakdown, .lookup-overview, .plan-detail-grid { grid-template-columns: 1fr; }
      .access-code-card { grid-template-columns: 1fr; align-items: stretch; }
      .access-card-actions { grid-template-columns: 1fr; }
      .audit-log-card { grid-template-columns: 1fr; }
      .plan-timeline { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .schedule-log-row { grid-template-columns: 1fr; }
      .cost-controls { align-items: stretch; flex-direction: column; }
      .cost-tabs { width: 100%; }
      .cost-tab { flex: 1; }
      .cost-select { width: 100%; }
      .cost-row { grid-template-columns: 1fr; gap: 6px; }
      .cost-row-amount { justify-self: start; }
      .admin-sidebar { position: static; }
      .chart-legend { grid-template-columns: 1fr; }
      .login-visual { min-height: auto; padding: 28px; }
      .login-visual h1 { white-space: normal; }
      .login-card { padding: 28px; }
      .facts { grid-template-columns: 1fr; }
      .next-row { grid-template-columns: 1fr; gap: 4px; }
      table { min-width: 680px; }
    }
    @media (max-width: 900px) {
      .access-table-wrap { border: 0; background: transparent; overflow: visible; }
      .access-table-wrap table,
      .access-table-wrap thead,
      .access-table-wrap tbody,
      .access-table-wrap tr,
      .access-table-wrap th,
      .access-table-wrap td {
        display: block;
        width: 100%;
        min-width: 0;
      }
      .access-table-wrap table { min-width: 0; }
      .access-table-wrap thead { display: none; }
      .access-table-wrap tr {
        position: relative;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fff;
        box-shadow: 0 10px 24px rgba(0, 43, 88, .07);
        margin-bottom: 12px;
        overflow: hidden;
      }
      .access-table-wrap tr.access-active { border-left: 4px solid #159957; }
      .access-table-wrap tr.access-expired { border-left: 4px solid #d91f3f; }
      .access-table-wrap td {
        display: grid;
        grid-template-columns: 112px minmax(0, 1fr);
        gap: 10px;
        padding: 10px 12px;
        border-bottom: 1px solid var(--line);
        white-space: normal;
        overflow-wrap: anywhere;
      }
      .access-table-wrap td::before {
        content: attr(data-label);
        color: var(--muted);
        font-size: 11px;
        font-weight: 800;
        text-transform: uppercase;
      }
      .access-table-wrap td:first-child {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 13px 12px;
        background: #f7fbff;
        border-left: 0;
        font-size: 18px;
      }
      .access-table-wrap td:first-child::before {
        content: "Mã truy cập";
        font-size: 12px;
      }
      .access-table-wrap td:last-child { border-bottom: 0; }
      .access-table-wrap .plate-list-cell {
        line-height: 1.5;
      }
      .access-table-wrap .row-actions,
      .access-table-wrap .extend-form {
        min-width: 0;
        width: 100%;
      }
      .access-table-wrap .extend-form input {
        width: 76px;
      }
      .access-table-wrap tr.access-active td,
      .access-table-wrap tr.access-expired td {
        color: var(--ink);
      }
      .access-table-wrap tr.access-active td:not(:first-child),
      .access-table-wrap tr.access-expired td:not(:first-child) {
        background: #fff;
      }
      .access-table-wrap tr.access-active td:first-child {
        background: #eaf8f0;
      }
      .access-table-wrap tr.access-expired td:first-child {
        background: #fff0f2;
      }
      .access-table-wrap tr.access-active td:first-child,
      .access-table-wrap tr.access-expired td:first-child {
        border-left: 0;
      }
    }
    @media (max-width: 640px) {
      body { background: #eef5fb; }
      .topbar {
        position: sticky;
        width: 100%;
        padding: 12px;
        border-bottom-width: 3px;
        box-shadow: 0 8px 20px rgba(0, 54, 108, .14);
      }
      .brand { gap: 9px; min-width: 0; }
      .brand-logo img { height: 36px; }
      .brand-name { font-size: 21px; }
      .brand-kicker { font-size: 9px; }
      .btn { width: 100%; min-height: 42px; padding: 10px 9px; font-size: 13px; text-align: center; }
      .pagination {
        align-items: stretch;
        gap: 8px;
      }
      .pagination > .subtle {
        width: 100%;
      }
      .pagination-pages {
        width: 100%;
        flex-wrap: nowrap;
        overflow-x: auto;
        padding: 1px 1px 4px;
        scrollbar-width: thin;
        -webkit-overflow-scrolling: touch;
      }
      .pagination .btn {
        width: auto;
        min-width: 42px;
        flex: 0 0 auto;
        min-height: 40px;
        padding: 9px 12px;
      }
      .pagination .btn:first-child,
      .pagination .btn:last-child {
        min-width: 68px;
      }
      .page { width: calc(100% - 20px); margin: 12px auto 24px; }
      .login-page { padding: 12px; place-items: stretch; }
      .login-wrap { align-self: center; }
      .login-visual, .login-card { padding: 22px; }
      .login-visual h1 { font-size: 24px; }
      .login-stats { grid-template-columns: 1fr; }
      h1 { font-size: 23px; line-height: 1.18; }
      h2 { font-size: 17px; }
      .hero { gap: 12px; margin-bottom: 10px; }
      .dashboard-hero {
        margin-bottom: 8px;
      }
      .dashboard-hero .subtle {
        display: none;
      }
      .hero > .toolbar .btn {
        min-height: 38px;
        padding: 9px 12px;
      }
      .dashboard-stats {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        margin-bottom: 10px;
      }
      .dashboard-stat {
        min-height: 84px;
        padding: 10px 11px;
        border-left-width: 3px;
      }
      .dashboard-stat span {
        min-height: 30px;
        margin-bottom: 3px;
        font-size: 11px;
        line-height: 1.25;
      }
      .dashboard-stat strong {
        font-size: 22px;
        line-height: 1.05;
      }
      .section-head { align-items: stretch; flex-direction: column; }
      .toolbar, .hero > .toolbar { width: 100%; gap: 8px; }
      .toolbar > * { flex: 1 1 100%; }
      .admin-sidebar {
        gap: 8px;
      }
      .admin-sidebar > div {
        display: none;
      }
      .admin-menu {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .admin-menu a {
        display: flex;
        min-height: 48px;
        align-items: center;
        justify-content: center;
        padding: 10px;
        text-align: center;
      }
      .vehicle-list-tools {
        flex-direction: row;
        flex-wrap: nowrap;
        align-items: center;
      }
      .vehicle-list-tools .vehicle-search {
        flex: 1 1 auto;
        min-width: 0;
      }
      .vehicle-list-tools .badge {
        flex: 0 0 auto;
      }
      .vehicle-controls {
        grid-template-columns: 1fr auto;
        gap: 8px;
      }
      .vehicle-controls .vehicle-search {
        grid-column: 1 / -1;
        width: 100%;
      }
      .vehicle-controls .btn {
        min-height: 40px;
        padding: 10px 12px;
      }
      .vehicle-table-action .btn {
        width: auto;
        min-height: 38px;
      }
      .combo { min-width: 0; width: 100%; }
      .panel { padding: 13px; box-shadow: 0 8px 22px rgba(0, 43, 88, .08); }
      .dashboard-stat strong { font-size: 22px; }
      .chart-title { align-items: flex-start; flex-direction: column; gap: 4px; }
      .cost-category-grid { grid-template-columns: 1fr; }
      .cost-pie-mini-panel { grid-template-columns: 1fr; }
      .cost-pie-wrap { justify-content: flex-start; }
      .vehicle-card { grid-template-columns: 1fr; }
      .vehicle-card .badge { justify-self: start; }
      .form-row { grid-template-columns: 1fr; }
      .row-actions, .extend-form { width: 100%; flex-direction: column; align-items: stretch; }
      .extend-form input { width: 100%; }
      .table-wrap { border: 0; background: transparent; overflow: visible; }
      .table-wrap table, .table-wrap thead, .table-wrap tbody, .table-wrap tr, .table-wrap th, .table-wrap td {
        display: block;
        width: 100%;
        min-width: 0;
      }
      .table-wrap table { min-width: 0; }
      .table-wrap thead { display: none; }
      .table-wrap tr {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fff;
        box-shadow: 0 10px 24px rgba(0, 43, 88, .07);
        margin-bottom: 10px;
        overflow: hidden;
      }
      .table-wrap td {
        display: grid;
        grid-template-columns: minmax(98px, 36%) minmax(0, 1fr);
        gap: 10px;
        padding: 10px 12px;
        border-bottom: 1px solid var(--line);
        white-space: normal;
        overflow-wrap: anywhere;
      }
      .table-wrap td::before {
        content: attr(data-label);
        color: var(--muted);
        font-size: 12px;
        font-weight: 800;
        text-transform: uppercase;
      }
      .table-wrap tr:last-child td { border-bottom: 1px solid var(--line); }
      .table-wrap td:last-child { border-bottom: 0; }
      .admin-data-table-wrap tr {
        border-left: 3px solid #0a72bd;
      }
      .admin-data-table-wrap td:first-child {
        background: #f7fbff;
        font-weight: 800;
      }
      .admin-data-table-wrap td[data-label="Thao tác"] .btn {
        width: 100%;
      }
      tr.access-active td:first-child, tr.access-expired td:first-child { border-left: 0; }
      .access-table-wrap td:first-child {
        display: flex;
        font-size: 17px;
      }
      .access-table-wrap td:first-child::before {
        content: "Mã truy cập";
      }
      .access-table-wrap td {
        grid-template-columns: 96px minmax(0, 1fr);
      }
      .access-table-wrap .access-remaining-cell {
        align-items: center;
      }
      .access-table-wrap .access-remaining-content {
        min-width: 0;
        flex-wrap: nowrap;
      }
      .access-table-wrap .access-remaining-text {
        font-size: 14px;
      }
      .access-table-wrap td[data-label="Thao tác"] {
        display: block;
      }
      .access-table-wrap td[data-label="Thao tác"]::before {
        display: block;
        margin-bottom: 8px;
      }
      .access-table-wrap .access-actions {
        width: 100%;
        min-width: 0;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      }
      .access-table-wrap .extend-hours {
        grid-column: 1 / -1;
      }
      .access-table-wrap .access-actions .btn {
        min-height: 42px;
      }
      .access-table-wrap .row-actions {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 8px;
        align-items: stretch;
      }
      .access-table-wrap .extend-form {
        display: grid;
        grid-template-columns: 72px minmax(0, 1fr);
        gap: 8px;
      }
      .access-table-wrap .extend-form input,
      .access-table-wrap .extend-form .btn,
      .access-table-wrap .row-actions .btn {
        min-height: 40px;
      }
      .access-table-wrap .row-actions.access-actions {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      }
      .access-table-wrap .access-actions .access-extend-form,
      .access-table-wrap .access-actions .access-revoke-form {
        display: contents;
      }
      .access-table-wrap .access-actions .extend-hours {
        grid-column: 1 / -1;
      }
      .access-table-wrap .access-actions .btn {
        width: 100%;
        min-height: 42px;
      }
    }
    @media (max-width: 420px) {
      .login-visual, .login-card { padding: 18px; }
      .metric-card, .dashboard-stat, .fact { padding: 11px; }
      .dashboard-stats {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .dashboard-stat {
        min-height: 82px;
      }
      .dashboard-stat span {
        font-size: 11px;
      }
      .table-wrap td { grid-template-columns: 1fr; gap: 5px; }
      .access-table-wrap td { grid-template-columns: 1fr; }
      .access-table-wrap .access-remaining-cell {
        grid-template-columns: 84px minmax(0, 1fr);
      }
      .access-table-wrap .row-actions,
      .access-table-wrap .extend-form {
        grid-template-columns: 1fr;
      }
      .access-table-wrap .extend-form input {
        width: 100%;
      }
      .access-table-wrap .row-actions.access-actions {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      }
      .access-table-wrap .access-actions .extend-hours {
        grid-column: 1 / -1;
      }
    }
    @media (max-width: 820px) {
      .admin-management-panel {
        min-height: auto;
      }
      .access-code-card,
      .audit-log-card {
        border-radius: 8px;
        padding: 13px;
        gap: 12px;
      }
      .access-code-main,
      .access-code-meta,
      .access-code-status,
      .audit-log-time,
      .audit-log-body,
      .audit-log-meta {
        gap: 4px;
      }
      .access-copy-code {
        width: 100%;
        justify-content: space-between;
      }
      .access-code-main .status-pill {
        justify-self: start;
        max-width: 100%;
      }
      .access-card-actions {
        width: 100%;
        justify-self: stretch;
        padding-top: 2px;
      }
      .audit-log-detail {
        font-size: 14px;
      }
      .inline-modal-backdrop,
      .dashboard-modal-backdrop {
        padding: 12px;
        align-items: flex-start;
        overflow-y: auto;
      }
      .inline-modal,
      .dashboard-modal {
        width: 100%;
        max-height: calc(100vh - 24px);
        overflow-y: auto;
        border-radius: 8px;
      }
      .inline-modal-body,
      .dashboard-modal-body {
        padding: 14px;
      }
    }
    @media (max-width: 640px) {
      input,
      select,
      textarea {
        font-size: 16px;
      }
      .admin-shell,
      .lookup-overview,
      .cost-panel,
      .dashboard-grid,
      .grid {
        gap: 10px;
      }
      .admin-menu {
        grid-template-columns: 1fr;
      }
      .admin-menu a {
        justify-content: flex-start;
        text-align: left;
      }
      .section-head .toolbar {
        align-items: stretch;
      }
      .access-code-card,
      .audit-log-card {
        box-shadow: 0 8px 18px rgba(0, 43, 88, .06);
      }
      .access-code-main .code {
        font-size: 17px;
      }
      .access-copy-hint {
        flex: 0 0 auto;
      }
      .access-card-actions .extend-hours,
      .access-card-actions .btn,
      .row-actions .btn,
      .history-actions .btn {
        min-height: 44px;
      }
      .cost-total-card strong {
        font-size: 30px;
      }
      .cost-pie-mini-panel {
        padding: 12px;
      }
      .cost-pie {
        width: 116px;
      }
      .dashboard-modal-row {
        grid-template-columns: 1fr;
      }
      .dashboard-modal-meta {
        justify-self: start;
      }
    }
  </style>
</head>
<body class="{% if active == 'dashboard' %}dashboard-page{% endif %}">
{% if login_page %}
  {{ content|safe }}
{% else %}
  <div class="shell">
    <header class="topbar">
      <a class="brand" href="{{ url_for('dashboard') }}">
        <span class="brand-logo">
          <img src="{{ url_for('static', filename='vietinbank-mark.png') }}" alt="VietinBank">
        </span>
        <span class="brand-text">
          <span class="brand-name">Vietin<strong>Bank</strong></span>
          <span class="brand-kicker">QUẢN LÝ XE</span>
        </span>
      </a>
      <button class="menu-toggle" type="button" aria-label="Mở menu" aria-controls="site-nav" aria-expanded="false">
        <span aria-hidden="true"></span>
      </button>
      <div class="nav-overlay" data-menu-close></div>
      <nav class="nav" id="site-nav">
        <span class="nav-title">Menu</span>
        <a class="{{ 'active' if active == 'dashboard' else '' }}" href="{{ url_for('dashboard') }}">Dashboard</a>
        <a class="{{ 'active' if active == 'detail' else '' }}" href="{{ url_for('detail') }}">Chi tiết</a>
        {% if is_admin %}
          <a class="{{ 'active' if active in ['data', 'admin'] else '' }}" href="{{ url_for('admin_data') }}">Quản trị</a>
        {% endif %}
        <a href="{{ url_for('logout') }}">Đăng xuất</a>
      </nav>
    </header>
    <main class="page">
      {{ messages|safe }}
      {{ content|safe }}
    </main>
  </div>
  <script>
    (function () {
      var toggle = document.querySelector(".menu-toggle");
      var nav = document.getElementById("site-nav");
      if (!toggle || !nav) return;

      function setMenu(open) {
        document.body.classList.toggle("menu-open", open);
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      }

      toggle.addEventListener("click", function () {
        setMenu(!document.body.classList.contains("menu-open"));
      });

      document.addEventListener("click", function (event) {
        if (event.target.closest("[data-menu-close]")) {
          setMenu(false);
        }
        if (event.target.closest("#site-nav a")) {
          setMenu(false);
        }
      });

      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          setMenu(false);
        }
      });
    })();
  </script>
  <script>
    (function () {
      var timeoutMs = {{ inactivity_timeout_ms|default(900000) }};
      var logoutUrl = {{ logout_url|tojson }};
      var timer = null;

      function scheduleLogout() {
        if (timer) clearTimeout(timer);
        timer = setTimeout(function () {
          window.location.href = logoutUrl;
        }, timeoutMs);
      }

      ["click", "mousemove", "keydown", "scroll", "touchstart", "mousedown"].forEach(function (eventName) {
        document.addEventListener(eventName, scheduleLogout, { passive: true });
      });

      window.addEventListener("focus", scheduleLogout);
      scheduleLogout();
    })();
  </script>
{% endif %}
</body>
</html>
"""


def flash_messages():
    rows = []
    for category, message in list(get_flashed_messages_with_categories()):
        rows.append(f'<div class="message {category}">{message}</div>')
    return f'<div class="messages">{"".join(rows)}</div>' if rows else ""


def get_flashed_messages_with_categories():
    from flask import get_flashed_messages

    return get_flashed_messages(with_categories=True)


def page(content, title="Quản lý xe Vietinbank", active="detail", login_page=False):
    return render_template_string(
        BASE_TEMPLATE,
        title=title,
        content=content,
        active=active,
        login_page=login_page,
        logout_url=url_for("logout"),
        inactivity_timeout_ms=INACTIVITY_TIMEOUT_MINUTES * 60 * 1000,
        is_admin=is_admin(),
        messages=flash_messages(),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if ADMIN_KEY and code == ADMIN_KEY:
            session["access_info"] = {"code": ADMIN_KEY, "bien_so": "ALL", "cap_time": None}
            touch_last_activity()
            audit_log("login", "Đăng nhập quản trị")
            return redirect(url_for("dashboard"))

        df = worksheet_df(SHEET_ACCESS, ["MaTruyCap", "BienSo", "ThoiDiemCap", "ThoiHanGio"])
        row = df[df["MaTruyCap"].astype(str) == code] if not df.empty else pd.DataFrame()

        if row.empty:
            flash("Mã truy cập không tồn tại.", "error")
        else:
            ttl_hours = parse_ttl_hours(row.iloc[0].get("ThoiHanGio", ACCESS_TTL_HOURS))
            cap_time = parse_cap_time(row.iloc[0]["ThoiDiemCap"])
            if now_vn() > cap_time + timedelta(hours=ttl_hours):
                flash("Mã truy cập đã hết hạn.", "error")
            else:
                session["access_info"] = {
                    "code": code,
                    "bien_so": str(row.iloc[0]["BienSo"]),
                    "cap_time": str(row.iloc[0]["ThoiDiemCap"]),
                    "ttl_hours": ttl_hours,
                }
                touch_last_activity()
                audit_log("login", f"Đăng nhập mã truy cập cho {row.iloc[0]['BienSo']}")
                return redirect(url_for("dashboard"))

    content = render_template_string(
        r"""
        <div class="login-page">
          <div class="login-wrap">
            <section class="login-visual">
              <div>
                <div class="brand" style="margin-bottom:36px">
                  <span class="brand-logo">
                    <img src="{{ url_for('static', filename='vietinbank-mark.png') }}" alt="VietinBank">
                  </span>
                  <span class="brand-text">
                    <span class="brand-name">Vietin<strong>Bank</strong></span>
                    <span class="brand-kicker">QUẢN LÝ XE</span>
                  </span>
                </div>
                <h1>Cổng tra cứu bảo dưỡng xe</h1>
                <p>Theo dõi thông tin phương tiện, lịch bảo dưỡng và chi phí vận hành.</p>
              </div>
              <div class="login-stats">
                <div class="login-stat"><strong>Mã truy cập</strong><span>Hiệu lực trong 24 giờ</span></div>
                <div class="login-stat"><strong>Báo cáo Excel</strong><span>Tải lịch sử bảo dưỡng</span></div>
              </div>
            </section>
            <form class="login-card" method="post">
              {{ messages|safe }}
              <h1>Đăng nhập</h1>
              <p>Nhập mã truy cập được cấp để tiếp tục.</p>
              <div class="field">
                <label for="code">Mã truy cập</label>
                <input id="code" name="code" type="password" autocomplete="current-password" autofocus>
              </div>
              <button class="btn primary full" type="submit">Đăng nhập</button>
            </form>
          </div>
        </div>
        """,
        messages=flash_messages(),
    )
    return page(content, title="Đăng nhập", login_page=True)


@app.route("/logout")
def logout():
    audit_log("logout", "Đăng xuất")
    session.clear()
    return redirect(url_for("login"))


@app.route("/refresh")
@login_required
def refresh_data():
    clear_cache()
    return redirect(request.referrer or url_for("detail"))


@app.route("/dashboard")
@login_required
def dashboard():
    data = load_data(["xe", "history", "next"])
    plates = allowed_plates(data)
    if not plates:
        content = """
        <section class="hero"><div><h1>Dashboard</h1><p class="subtle">Không có biển số nào được cấp quyền.</p></div></section>
        <div class="empty">Chưa có dữ liệu xe để hiển thị.</div>
        """
        return page(content, active="dashboard")

    rows = dashboard_rows(data, plates)
    total = len(rows)
    registry_overdue_rows = [row for row in rows if row["registry"]["key"] == "overdue"]
    due_popup_rows = []
    for row in rows:
        if row["registry"]["key"] == "due":
            due_popup_rows.append(
                {
                    "plate": row["plate"],
                    "car_type": row["car_type"],
                    "status": row["status"],
                    "kind": "Đăng kiểm",
                    "date": row["registry"].get("date", ""),
                    "label": status_text(row["registry"]),
                }
            )
        if row["maintenance"]["key"] == "due":
            due_popup_rows.append(
                {
                    "plate": row["plate"],
                    "car_type": row["car_type"],
                    "status": row["status"],
                    "kind": "Bảo dưỡng",
                    "date": row["maintenance"].get("date", ""),
                    "label": status_text(row["maintenance"]),
                }
            )
    history_popup_rows = []
    plate_col = XE_COLUMNS[0]
    if not data["history"].empty and plate_col in data["history"].columns:
        history_view = data["history"][data["history"][plate_col].astype(str).isin(plates)].copy()
        history_popup_rows = build_history_rows(history_view)
    dashboard_modal_groups = [
        {"key": "all", "title": "Tất cả xe", "rows": rows},
        {"key": "registry-overdue", "title": "Quá hạn đăng kiểm", "rows": registry_overdue_rows},
        {"key": "attention", "title": "Hạng mục sắp đến hạn", "rows": due_popup_rows, "row_type": "due", "unit": "lượt"},
        {"key": "history", "title": "Lịch sử bảo dưỡng", "rows": history_popup_rows, "row_type": "history", "unit": "lượt"},
    ]
    dashboard_query = request.args.get("q", "").strip()
    filtered_rows = rows
    if dashboard_query:
        normalized_query = dashboard_query.lower()
        filtered_rows = [
            row
            for row in rows
            if normalized_query in str(row["plate"]).lower()
            or normalized_query in str(row["car_type"]).lower()
        ]
    dashboard_filter = "all"
    dashboard_page_sizes = [10, 20, 50, 100]
    try:
        dashboard_per_page = int(request.args.get("dashboard_per_page", 10))
    except ValueError:
        dashboard_per_page = 10
    if dashboard_per_page not in dashboard_page_sizes:
        dashboard_per_page = 10
    try:
        dashboard_page = int(request.args.get("dashboard_page", 1))
    except ValueError:
        dashboard_page = 1
    dashboard_total_pages = max(1, (len(filtered_rows) + dashboard_per_page - 1) // dashboard_per_page)
    if dashboard_page < 1:
        dashboard_page = 1
    if dashboard_page > dashboard_total_pages:
        dashboard_page = dashboard_total_pages
    dashboard_start = (dashboard_page - 1) * dashboard_per_page
    dashboard_end = min(dashboard_start + dashboard_per_page, len(filtered_rows))
    dashboard_page_rows = filtered_rows[dashboard_start:dashboard_end]
    dashboard_list_total = len(filtered_rows)
    maintenance_counts = count_due_states(rows, "maintenance")
    registry_counts = count_due_states(rows, "registry")
    maintenance_segments = build_status_segments(maintenance_counts, total)
    registry_segments = build_status_segments(registry_counts, total)
    attention_rows = [
        row
        for row in rows
        if row["maintenance"]["key"] in ("overdue", "due")
        or row["registry"]["key"] in ("overdue", "due")
    ]
    due_total = len(due_popup_rows)
    total_history = len(history_popup_rows)
    today = now_vn()
    current_quarter = ((today.month - 1) // 3) + 1
    cost_period = request.args.get("cost_period", "year")
    cost_month = request.args.get("cost_month") or today.strftime("%Y-%m")
    cost_quarter = request.args.get("cost_quarter") or f"{today.year}-Q{current_quarter}"
    cost_year = request.args.get("cost_year") or str(today.year)
    selected_cost_value = {
        "month": cost_month,
        "quarter": cost_quarter,
        "year": cost_year,
    }.get(cost_period, cost_year)
    cost_stats = dashboard_cost_summary(data, plates, cost_period, selected_cost_value)
    cost_chart_total = sum(float(item.get("raw") or 0) for item in cost_stats.get("categories", []))
    cost_chart_colors = {
        "repair": "#d91f3f",
        "periodic": "#159957",
        "oil": "#d88900",
        "registry": "#0b75bb",
    }
    cost_chart_segments = []
    cursor = 0.0
    for item in cost_stats.get("categories", []):
        raw = float(item.get("raw") or 0)
        if raw <= 0:
            continue
        share = (raw / cost_chart_total * 100) if cost_chart_total else 0
        segment = dict(item)
        segment["color"] = cost_chart_colors.get(item.get("key"), "#9aa9b8")
        segment["start"] = round(cursor, 4)
        segment["percent"] = round(share, 1)
        segment["percent_label"] = f"{share:.1f}%".replace(".0%", "%")
        cursor += share
        segment["end"] = round(cursor, 4)
        cost_chart_segments.append(segment)
    if cost_chart_segments:
        cost_chart_segments[-1]["end"] = 100.0
    cost_chart_gradient = ", ".join(
        f"{segment['color']} {segment['start']}% {segment['end']}%" for segment in cost_chart_segments
    ) if cost_chart_segments else "#e7eef6 0% 100%"
    dashboard_modal_groups.extend(cost_stats.get("category_groups", []))

    content = render_template_string(
        r"""
        <section class="hero dashboard-hero">
          <div>
            <h1>Dashboard đội xe</h1>
            <p class="subtle">Dashboard đăng kiểm, bảo dưỡng và các xe cần chú ý.</p>
          </div>
        </section>

        <section class="dashboard-stats">
          <button class="dashboard-stat dashboard-stat-linkcard" type="button" data-dashboard-modal="all">
            <span>Tổng số xe</span>
            <strong>{{ total }}</strong>
          </button>
          <button class="dashboard-stat danger dashboard-stat-linkcard" type="button" data-dashboard-modal="registry-overdue">
            <span>Quá hạn đăng kiểm</span>
            <strong>{{ registry_counts.overdue }}</strong>
          </button>
          <button class="dashboard-stat warning dashboard-stat-linkcard" type="button" data-dashboard-modal="attention">
            <span>Sắp đến hạn</span>
            <strong>{{ due_total }}</strong>
          </button>
          <button class="dashboard-stat ok dashboard-stat-linkcard" type="button" data-dashboard-modal="history">
            <span>Lượt bảo dưỡng đã ghi nhận</span>
            <strong>{{ total_history }}</strong>
          </button>
        </section>

        <div class="dashboard-modal-backdrop" id="dashboard-stat-modal" hidden>
          <div class="dashboard-modal" role="dialog" aria-modal="true" aria-labelledby="dashboard-modal-title">
            <div class="dashboard-modal-head">
              <h3 id="dashboard-modal-title" data-dashboard-modal-title>Danh sách xe</h3>
              <button class="dashboard-modal-close" type="button" data-dashboard-modal-close aria-label="Đóng">×</button>
            </div>
            <div class="dashboard-modal-body">
              {% for group in dashboard_modal_groups %}
                <section class="dashboard-modal-panel" data-dashboard-modal-panel="{{ group.key }}" data-title="{{ group.title }} ({{ group.rows|length }} {{ group.unit|default('xe') }})" hidden>
                  {% if group.rows %}
                    <div class="dashboard-modal-list">
                      {% for row in group.rows %}
                        {% if group.row_type|default('vehicle') == 'history' %}
                          <a class="dashboard-modal-row" href="{{ url_for('detail', plate=row.plate) }}">
                            <div class="dashboard-modal-main">
                              <strong>{{ row.plate }}</strong>
                              <span>{{ row.date }}</span>
                            </div>
                            <div class="dashboard-modal-status">
                              <span>{{ row.cost_type_label }}</span>
                              <span>{{ row.content }}</span>
                            </div>
                            <div class="dashboard-modal-meta">{{ row.cost }}</div>
                          </a>
                        {% elif group.row_type|default('vehicle') == 'due' %}
                          <a class="dashboard-modal-row" href="{{ url_for('detail', plate=row.plate) }}">
                            <div class="dashboard-modal-main">
                              <strong>{{ row.plate }}{% if row.car_type %} - {{ row.car_type }}{% endif %}</strong>
                              <span>{{ row.status }}</span>
                            </div>
                            <div class="dashboard-modal-status">
                              <span>{{ row.kind }}</span>
                              <span>{{ row.date }}</span>
                            </div>
                            <div class="dashboard-modal-meta">{{ row.label }}</div>
                          </a>
                        {% elif group.row_type|default('vehicle') == 'cost' %}
                          <a class="dashboard-modal-row" href="{{ url_for('detail', plate=row.plate) }}">
                            <div class="dashboard-modal-main">
                              <strong>{{ row.plate }}{% if row.car_type %} - {{ row.car_type }}{% endif %}</strong>
                              <span>{{ row.date }}</span>
                            </div>
                            <div class="dashboard-modal-status">
                              <span>{{ row.cost_type_label }}</span>
                              <span>{{ row.content }}</span>
                            </div>
                            <div class="dashboard-modal-meta">{{ row.cost }}</div>
                          </a>
                        {% else %}
                          <a class="dashboard-modal-row" href="{{ url_for('detail', plate=row.plate) }}">
                            <div class="dashboard-modal-main">
                              <strong>{{ row.plate }}</strong>
                              <span>{{ row.car_type }}</span>
                            </div>
                            <div class="dashboard-modal-status">
                              <span>{{ row.registry.label }}: {{ row.registry.date }}</span>
                              <span>{{ row.maintenance.label }}: {{ row.maintenance.date }}</span>
                            </div>
                            <div class="dashboard-modal-meta">{{ row.total_cost }}</div>
                          </a>
                        {% endif %}
                      {% endfor %}
                    </div>
                  {% else %}
                    <div class="empty-state">Không có dữ liệu phù hợp.</div>
                  {% endif %}
                </section>
              {% endfor %}
            </div>
          </div>
        </div>
        <script>
          (() => {
            const modal = document.getElementById("dashboard-stat-modal");
            if (!modal) return;
            const title = modal.querySelector("[data-dashboard-modal-title]");
            const panels = Array.from(modal.querySelectorAll("[data-dashboard-modal-panel]"));
            const closeButtons = modal.querySelectorAll("[data-dashboard-modal-close]");

            const openModal = (key) => {
              const panel = panels.find((item) => item.dataset.dashboardModalPanel === key);
              if (!panel) return;
              panels.forEach((item) => { item.hidden = item !== panel; });
              title.textContent = panel.dataset.title || "Danh sách xe";
              modal.hidden = false;
              requestAnimationFrame(() => modal.classList.add("open"));
            };

            const closeModal = () => {
              modal.classList.remove("open");
              modal.hidden = true;
            };

            document.addEventListener("click", (event) => {
              const trigger = event.target.closest("[data-dashboard-modal]");
              if (trigger) openModal(trigger.dataset.dashboardModal);
            });
            closeButtons.forEach((button) => button.addEventListener("click", closeModal));
            modal.addEventListener("click", (event) => {
              if (event.target === modal) closeModal();
            });
            document.addEventListener("keydown", (event) => {
              if (event.key === "Escape" && !modal.hidden) closeModal();
            });
          })();
        </script>

        <section class="panel cost-overview-panel">
          <div class="section-head">
            <div>
              <h2>Chi phí bảo dưỡng</h2>
              <p class="subtle">Tổng quan chi phí theo tháng, quý hoặc năm.</p>
            </div>
            <span class="badge">{{ cost_stats.label }}</span>
          </div>
          <div class="cost-controls">
            <div class="cost-tabs" role="tablist" aria-label="Chọn kỳ chi phí">
              <a class="cost-tab {{ 'active' if cost_stats.period == 'month' else '' }}" href="{{ url_for('dashboard', cost_period='month', cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, q=dashboard_query) }}">Tháng</a>
              <a class="cost-tab {{ 'active' if cost_stats.period == 'quarter' else '' }}" href="{{ url_for('dashboard', cost_period='quarter', cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, q=dashboard_query) }}">Quý</a>
              <a class="cost-tab {{ 'active' if cost_stats.period == 'year' else '' }}" href="{{ url_for('dashboard', cost_period='year', cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, q=dashboard_query) }}">Năm</a>
            </div>
            <form method="get" action="{{ url_for('dashboard') }}">
              <input type="hidden" name="cost_period" value="{{ cost_stats.period }}">
              <input type="hidden" name="q" value="{{ dashboard_query }}">
              {% if cost_stats.period == 'month' %}
                <input type="hidden" name="cost_quarter" value="{{ cost_quarter }}">
                <input type="hidden" name="cost_year" value="{{ cost_year }}">
                <input class="cost-select" type="month" name="cost_month" value="{{ cost_stats.selected }}" onchange="this.form.submit()">
              {% elif cost_stats.period == 'quarter' %}
                <input type="hidden" name="cost_month" value="{{ cost_month }}">
                <input type="hidden" name="cost_year" value="{{ cost_year }}">
                <select class="cost-select" name="cost_quarter" onchange="this.form.submit()">
                  {% for option in cost_stats.quarter_options %}
                    <option value="{{ option.value }}" {% if option.value == cost_stats.selected %}selected{% endif %}>{{ option.label }}</option>
                  {% endfor %}
                </select>
              {% else %}
                <input type="hidden" name="cost_month" value="{{ cost_month }}">
                <input type="hidden" name="cost_quarter" value="{{ cost_quarter }}">
                <select class="cost-select" name="cost_year" onchange="this.form.submit()">
                  {% for option in cost_stats.year_options %}
                    <option value="{{ option }}" {% if option == cost_stats.selected %}selected{% endif %}>Năm {{ option }}</option>
                  {% endfor %}
                </select>
              {% endif %}
            </form>
          </div>
          <div class="cost-panel">
            <div class="cost-summary cost-summary-chart">
              <div class="cost-total-card">
                <span>Tổng chi phí</span>
                <strong>{{ cost_stats.total }}</strong>
                <small>{{ cost_stats.date_label }}</small>
              </div>
              <div class="cost-category-grid">
                {% for item in cost_stats.categories %}
                  <button class="cost-category-card {{ item.key }}" type="button" data-dashboard-modal="cost-{{ item.key }}">
                    <span>{{ item.label }}</span>
                    <strong>{{ item.amount }}</strong>
                  </button>
                {% endfor %}
              </div>
              <div class="cost-pie-mini-panel">
                <div class="cost-pie-wrap">
                  <div class="cost-pie" style="background: conic-gradient({{ cost_chart_gradient }});">
                    <div class="cost-pie-center" aria-hidden="true"></div>
                  </div>
                </div>
                <div class="cost-pie-legend">
                  {% for item in cost_chart_segments %}
                    <button class="cost-pie-legend-item" type="button" data-dashboard-modal="cost-{{ item.key }}">
                      <span class="cost-pie-swatch" style="background: {{ item.color }}"></span>
                      <div class="cost-pie-legend-text">
                        <strong>{{ item.label }}</strong>
                        <span>{{ item.amount }}</span>
                      </div>
                      <span class="cost-pie-percent">{{ item.percent_label }}</span>
                    </button>
                  {% endfor %}
                </div>
              </div>
            </div>
            <div>
              <div class="chart-title">
                <strong>Top xe theo chi phí</strong>
                <span class="subtle">{{ cost_stats.record_count }} lượt ghi nhận</span>
              </div>
              {% if cost_stats.top_rows %}
                <div class="cost-chart">
                  {% for row in cost_stats.top_rows %}
                    <a class="cost-row" href="{{ url_for('detail', plate=row.plate) }}" title="{{ row.label }}">
                      <div class="cost-row-label">
                        <strong><span class="code">{{ row.plate }}</span>{% if row.car_type %} - {{ row.car_type }}{% endif %}</strong>
                        <span class="count-detail">
                          {% for part in row.count_parts %}
                            <span class="count-part {{ part.key }}">{{ part.text }}</span>
                          {% endfor %}
                        </span>
                      </div>
                      <div class="cost-row-track" aria-label="{{ row.plate }} {{ row.amount }}">
                        <span class="cost-row-fill" style="width: {{ row.percent }}%">
                          {% for segment in row.segments %}
                            <span
                              class="cost-row-segment {{ segment.key }}"
                              style="width: {{ segment.percent }}%"
                              title="{{ segment.label }}: {{ segment.amount }}"
                              aria-label="{{ segment.label }}: {{ segment.amount }}"
                            ></span>
                          {% endfor %}
                        </span>
                      </div>
                      <div class="cost-row-amount">{{ row.amount }}</div>
                    </a>
                  {% endfor %}
                </div>
              {% else %}
                <div class="empty">Chưa có chi phí bảo dưỡng trong kỳ này.</div>
              {% endif %}
            </div>
          </div>
        </section>

        <div class="dashboard-grid">
          <section class="panel">
            <div class="section-head">
              <h2>Biểu đồ trạng thái</h2>
              <span class="badge">{{ total }} xe</span>
            </div>
            <div class="chart-stack">
              <div>
                <div class="chart-title">
                  <strong>Đăng kiểm</strong>
                  <span class="subtle">{{ registry_counts.ok }} còn hạn</span>
                </div>
                <div class="chart-bar" aria-label="Biểu đồ đăng kiểm">
                  {% for segment in registry_segments %}
                    {% if segment.value %}
                      <span class="chart-segment {{ segment.key }}" style="width: {{ segment.percent }}%"></span>
                    {% endif %}
                  {% endfor %}
                </div>
                <div class="chart-legend">
                  {% for segment in registry_segments %}
                    <div class="legend-item">
                      <span class="legend-label"><span class="legend-dot {{ segment.key }}"></span>{{ segment.label }}</span>
                      <strong class="legend-value">{{ segment.value }}</strong>
                    </div>
                  {% endfor %}
                </div>
              </div>

              <div>
                <div class="chart-title">
                  <strong>Bảo dưỡng</strong>
                  <span class="subtle">{{ maintenance_counts.ok }} còn hạn</span>
                </div>
                <div class="chart-bar" aria-label="Biểu đồ bảo dưỡng">
                  {% for segment in maintenance_segments %}
                    {% if segment.value %}
                      <span class="chart-segment {{ segment.key }}" style="width: {{ segment.percent }}%"></span>
                    {% endif %}
                  {% endfor %}
                </div>
                <div class="chart-legend">
                  {% for segment in maintenance_segments %}
                    <div class="legend-item">
                      <span class="legend-label"><span class="legend-dot {{ segment.key }}"></span>{{ segment.label }}</span>
                      <strong class="legend-value">{{ segment.value }}</strong>
                    </div>
                  {% endfor %}
                </div>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="section-head">
              <h2>Xe cần chú ý</h2>
              <span class="badge">{{ attention_rows|length }} xe</span>
            </div>
            {% if attention_rows %}
              <div class="vehicle-list">
                {% for row in attention_rows %}
                  <a class="vehicle-card" href="{{ url_for('detail', plate=row.plate) }}">
                    <div>
                      <strong>{{ row.plate }}</strong>
                      <div class="vehicle-meta">{{ row.car_type }} · {{ row.status }}</div>
                    </div>
                    <span class="badge">{{ row.history_count }} lần</span>
                    <div class="vehicle-statuses">
                      <span class="due-pill {{ row.registry.key }}">Đăng kiểm: {{ status_text(row.registry) }}</span>
                      <span class="due-pill {{ row.maintenance.key }}">Bảo dưỡng: {{ status_text(row.maintenance) }}</span>
                    </div>
                  </a>
                {% endfor %}
              </div>
            {% else %}
              <div class="empty">Chưa có xe quá hạn hoặc sắp đến hạn trong 30 ngày.</div>
            {% endif %}
          </section>
        </div>

        <section class="panel">
          <div class="section-head">
            <div>
              <h2>Danh sách xe</h2>
              <p class="subtle">Theo dõi đăng kiểm, bảo dưỡng, số lần ghi nhận và chi phí.</p>
            </div>
            <span class="badge">{{ dashboard_list_total }} xe</span>
          </div>
          <div class="dashboard-list-controls">
            <form class="dashboard-list-search" method="get">
              <input type="hidden" name="cost_period" value="{{ cost_stats.period }}">
              <input type="hidden" name="cost_month" value="{{ cost_month }}">
              <input type="hidden" name="cost_quarter" value="{{ cost_quarter }}">
              <input type="hidden" name="cost_year" value="{{ cost_year }}">
              <input type="hidden" name="dashboard_per_page" value="{{ dashboard_per_page }}">
              <input type="hidden" name="dashboard_page" value="1">
              <div class="vehicle-search">
                <input id="dashboard-q" name="q" value="{{ dashboard_query }}" placeholder="Tìm biển số hoặc loại xe">
              </div>
              {% if dashboard_query %}
                <a class="btn" href="{{ url_for('dashboard', cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page) }}">Xóa lọc</a>
              {% endif %}
            </form>
            <span class="subtle">
              Hiển thị {{ dashboard_start + 1 if dashboard_list_total else 0 }}-{{ dashboard_end }} / {{ dashboard_list_total }} xe
            </span>
            <form class="dashboard-list-size" method="get">
              <input type="hidden" name="cost_period" value="{{ cost_stats.period }}">
              <input type="hidden" name="cost_month" value="{{ cost_month }}">
              <input type="hidden" name="cost_quarter" value="{{ cost_quarter }}">
              <input type="hidden" name="cost_year" value="{{ cost_year }}">
              <input type="hidden" name="q" value="{{ dashboard_query }}">
              <input type="hidden" name="dashboard_page" value="1">
              <label for="dashboard-per-page">Số xe/trang</label>
              <select class="cost-select" id="dashboard-per-page" name="dashboard_per_page">
                {% for size in dashboard_page_sizes %}
                  <option value="{{ size }}" {{ "selected" if size == dashboard_per_page else "" }}>{{ size }}</option>
                {% endfor %}
              </select>
            </form>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Biển số</th>
                  <th>Loại xe</th>
                  <th>Đăng kiểm</th>
                  <th>Bảo dưỡng</th>
                  <th>Lịch sử</th>
                  <th>Chi phí</th>
                </tr>
              </thead>
              <tbody>
                {% for row in dashboard_page_rows %}
                  <tr>
                    <td data-label="Biển số"><a class="code" href="{{ url_for('detail', plate=row.plate) }}">{{ row.plate }}</a></td>
                    <td data-label="Loại xe">{{ row.car_type }}</td>
                    <td data-label="Đăng kiểm">
                      <span class="due-pill {{ row.registry.key }}">{{ row.registry.label }}</span>
                      <div class="subtle">{{ row.registry.date }}</div>
                    </td>
                    <td data-label="Bảo dưỡng">
                      <span class="due-pill {{ row.maintenance.key }}">{{ row.maintenance.label }}</span>
                      <div class="subtle">{{ row.maintenance.date }}</div>
                    </td>
                    <td data-label="Lịch sử">{{ row.history_count }} lần</td>
                    <td class="cost" data-label="Chi phí">
                      <strong>{{ row.total_cost }}</strong>
                      <div class="cost-cell-breakdown">
                        {% for item in row.cost_breakdown %}
                          <span>{{ item.label }}: {{ item.amount }}</span>
                        {% endfor %}
                      </div>
                    </td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
          {% if dashboard_total_pages > 1 %}
            <div class="pagination">
              <span class="subtle">Trang {{ dashboard_page }}/{{ dashboard_total_pages }}</span>
              <div class="pagination-pages">
                <a class="btn {{ 'disabled' if dashboard_page <= 1 else '' }}" data-dashboard-preserve-scroll href="{{ url_for('dashboard', cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page, dashboard_page=dashboard_page - 1, q=dashboard_query) }}">&lsaquo; Trước</a>
                {% for p in range(1, dashboard_total_pages + 1) %}
                  <a class="btn {{ 'active' if p == dashboard_page else '' }}" data-dashboard-preserve-scroll href="{{ url_for('dashboard', cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page, dashboard_page=p, q=dashboard_query) }}">{{ p }}</a>
                {% endfor %}
                <a class="btn {{ 'disabled' if dashboard_page >= dashboard_total_pages else '' }}" data-dashboard-preserve-scroll href="{{ url_for('dashboard', cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page, dashboard_page=dashboard_page + 1, q=dashboard_query) }}">Sau &rsaquo;</a>
              </div>
            </div>
          {% endif %}
        </section>

        <script>
          (() => {
            const storageKey = "dashboardScrollY";
            const savedY = sessionStorage.getItem(storageKey);
            if (savedY !== null) {
              sessionStorage.removeItem(storageKey);
              requestAnimationFrame(() => {
                window.scrollTo({ top: Number(savedY) || 0, behavior: "instant" });
              });
            }

            const saveScroll = () => {
              sessionStorage.setItem(storageKey, String(window.scrollY || window.pageYOffset || 0));
            };

            document.querySelectorAll("[data-dashboard-preserve-scroll]").forEach((link) => {
              link.addEventListener("click", () => {
                if (!link.classList.contains("disabled")) saveScroll();
              });
            });

            const perPageSelect = document.getElementById("dashboard-per-page");
            perPageSelect?.addEventListener("change", () => {
              saveScroll();
              perPageSelect.form?.submit();
            });

            const searchForm = document.querySelector(".dashboard-list-search");
            const searchInput = document.getElementById("dashboard-q");
            let searchTimer = null;
            const submitSearch = () => {
              if (!searchForm) return;
              saveScroll();
              if (searchForm.requestSubmit) searchForm.requestSubmit();
              else searchForm.submit();
            };
            searchInput?.addEventListener("input", () => {
              if (searchTimer) clearTimeout(searchTimer);
              searchTimer = setTimeout(submitSearch, 250);
            });
          })();
        </script>
        """,
        total=total,
        due_total=due_total,
        total_history=total_history,
        rows=rows,
        dashboard_modal_groups=dashboard_modal_groups,
        dashboard_page_rows=dashboard_page_rows,
        dashboard_page_sizes=dashboard_page_sizes,
        dashboard_per_page=dashboard_per_page,
        dashboard_page=dashboard_page,
        dashboard_total_pages=dashboard_total_pages,
        dashboard_start=dashboard_start,
        dashboard_end=dashboard_end,
        dashboard_list_total=dashboard_list_total,
        dashboard_query=dashboard_query,
        attention_rows=attention_rows,
        maintenance_counts=maintenance_counts,
        registry_counts=registry_counts,
        maintenance_segments=maintenance_segments,
        registry_segments=registry_segments,
        cost_stats=cost_stats,
        cost_chart_segments=cost_chart_segments,
        cost_chart_gradient=cost_chart_gradient,
        cost_month=cost_month,
        cost_quarter=cost_quarter,
        cost_year=cost_year,
        status_text=status_text,
    )
    return page(content, title="Quản lý xe Vietinbank", active="dashboard")


@app.route("/")
def home():
    if current_access():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/detail")
@login_required
def detail():
    data = load_data(["xe", "history", "next"])
    plates = allowed_plates(data)
    if not plates:
        content = """
        <section class="hero"><div><h1>Tra cứu xe</h1><p class="subtle">Không có biển số nào được cấp quyền.</p></div></section>
        <div class="empty">Chưa có dữ liệu xe để hiển thị.</div>
        """
        return page(content)

    selected = request.args.get("plate") or session.get("selected_plate") or plates[0]
    if selected not in plates:
        selected = plates[0]
    session["selected_plate"] = selected

    df_xe = data["xe"]
    df_history = data["history"]
    df_next = data["next"]
    plate_labels = plate_label_map(df_xe)

    car_row = df_xe[df_xe["Biển số"].astype(str) == selected]
    car = car_row.iloc[0].to_dict() if not car_row.empty else {}

    next_row = df_next[df_next["Biển số"].astype(str) == selected]
    next_item = next_row.iloc[0].to_dict() if not next_row.empty else {}

    history_query = request.args.get("q", "").strip()
    date_from_raw = request.args.get("from", "").strip()
    date_to_raw = request.args.get("to", "").strip()
    history_view = df_history[df_history["Biển số"].astype(str) == selected].copy()
    if not history_view.empty:
        if history_query and "Nội dung" in history_view.columns:
            history_view = history_view[
                history_view["Nội dung"].astype(str).str.contains(history_query, case=False, na=False)
            ]
        date_from = parse_user_date(date_from_raw)
        date_to = parse_user_date(date_to_raw)
        if date_from is not None or date_to is not None:
            history_view["_date_filter"] = parse_date_series(history_view["Ngày"])
            if date_from is not None:
                history_view = history_view[history_view["_date_filter"] >= date_from]
            if date_to is not None:
                history_view = history_view[history_view["_date_filter"] <= date_to + timedelta(days=1)]
            history_view = history_view.drop(columns=["_date_filter"], errors="ignore")
    history_rows = build_history_rows(history_view)
    repair_count = len([row for row in history_rows if row.get("cost_type") == COST_TYPE_REPAIR])
    periodic_count = len([row for row in history_rows if row.get("cost_type") == COST_TYPE_PERIODIC])
    total_cost = sum(float(row["cost_raw"]) for row in history_rows)
    alerts = due_alerts(next_item) if next_item else []
    maintenance_state = due_state(next_item.get("Dự kiến lần tiếp theo")) if next_item else due_state("")
    registry_state = due_state(next_item.get("Hạn đăng kiểm đến")) if next_item else due_state("")
    schedule_logs = build_next_service_logs(selected)

    content = render_template_string(
        r"""
        <section class="hero">
          <div>
            <h1>Tra cứu lịch sử bảo dưỡng</h1>
            <p class="subtle">Biển số đang xem: <strong>{{ selected }}</strong></p>
          </div>
          <form class="toolbar" id="plate-picker-form" method="get">
            <input type="hidden" name="plate" id="plate-value" value="{{ selected }}">
            <div class="combo" id="plate-combo">
              <input
                id="plate-search"
                value="{{ plate_labels.get(selected, selected) }}"
                placeholder="Tìm biển số hoặc loại xe"
                autocomplete="off"
              >
              <button class="combo-toggle" type="button" aria-label="Mở danh sách xe">▼</button>
              <div class="combo-menu" id="plate-options">
                {% for plate in plates %}
                  <button
                    class="combo-option"
                    type="button"
                    data-plate="{{ plate }}"
                    data-label="{{ plate_labels.get(plate, plate) }}"
                  >{{ plate_labels.get(plate, plate) }}</button>
                {% endfor %}
              </div>
            </div>
          </form>
        </section>

        <script>
          (() => {
            const form = document.getElementById("plate-picker-form");
            const combo = document.getElementById("plate-combo");
            const search = document.getElementById("plate-search");
            const value = document.getElementById("plate-value");
            const toggle = combo?.querySelector(".combo-toggle");
            const options = Array.from(document.querySelectorAll("#plate-options .combo-option"));
            if (!form || !combo || !search || !value || !toggle) return;

            const openMenu = () => combo.classList.add("open");
            const closeMenu = () => combo.classList.remove("open");
            const filterOptions = (query = "") => {
              const normalized = query.trim().toLowerCase();
              options.forEach((option) => {
                const label = option.dataset.label.toLowerCase();
                const plate = option.dataset.plate.toLowerCase();
                option.style.display = !normalized || label.includes(normalized) || plate.includes(normalized) ? "" : "none";
              });
            };

            const syncPlate = () => {
              const typed = search.value.trim();
              const byLabel = options.find((option) => option.dataset.label === typed);
              const byPlate = options.find((option) => option.dataset.plate === typed);
              const match = byLabel || byPlate;
              if (!match) return false;
              value.value = match.dataset.plate;
              search.value = match.dataset.label;
              return true;
            };

            toggle.addEventListener("click", () => {
              filterOptions("");
              openMenu();
              search.focus();
            });
            search.addEventListener("focus", () => {
              search.select();
              filterOptions("");
              openMenu();
            });
            search.addEventListener("input", () => {
              filterOptions(search.value);
              openMenu();
            });
            options.forEach((option) => {
              option.addEventListener("click", () => {
                value.value = option.dataset.plate;
                search.value = option.dataset.label;
                closeMenu();
                form.submit();
              });
            });
            form.addEventListener("submit", (event) => {
              if (!syncPlate()) event.preventDefault();
            });
            document.addEventListener("click", (event) => {
              if (!combo.contains(event.target)) closeMenu();
            });
          })();
        </script>
        {% if can_manage_history %}
        <script>
          document.addEventListener("DOMContentLoaded", () => {
            const modal = document.getElementById("next-service-modal");
            if (!modal) return;
            const openButtons = document.querySelectorAll("[data-next-modal-open]");
            const editButtons = document.querySelectorAll("[data-next-log-edit]");
            const closeButtons = modal.querySelectorAll("[data-next-modal-close]");
            const title = modal.querySelector("[data-next-modal-title]");
            const actionInput = modal.querySelector("#next-modal-action");
            const recordIdInput = modal.querySelector("#next-modal-record-id");
            const submitButton = modal.querySelector("#next-modal-submit");
            const lastServiceInput = modal.querySelector("#modal-last-service");
            const nextDueInput = modal.querySelector("#modal-next-due");
            const registryDateInput = modal.querySelector("#modal-registry-date");
            const registryDueInput = modal.querySelector("#modal-registry-due");
            const suggestionInput = modal.querySelector("#modal-suggestion");
            const oilCostInput = modal.querySelector("#modal-oil-cost");
            const registryCostInput = modal.querySelector("#modal-registry-cost");

            const defaults = {
              title: "Thêm lịch bảo dưỡng và đăng kiểm",
              submit: "Thêm bản ghi",
              action: "save_next",
              recordId: "",
              lastService: "",
              nextDue: "",
              registryDate: "",
              registryDue: "",
              suggestion: "",
              oilCost: "",
              registryCost: "",
            };

            const open = () => {
              modal.hidden = false;
              requestAnimationFrame(() => modal.classList.add("open"));
            };
            const close = () => {
              modal.classList.remove("open");
              modal.hidden = true;
            };
            const setValue = (input, value) => {
              if (input) input.value = value ?? "";
            };
            const resetToAddMode = () => {
              if (title) title.textContent = defaults.title;
              if (submitButton) submitButton.textContent = defaults.submit;
              if (actionInput) actionInput.value = defaults.action;
              if (recordIdInput) recordIdInput.value = defaults.recordId;
              setValue(lastServiceInput, defaults.lastService);
              setValue(nextDueInput, defaults.nextDue);
              setValue(registryDateInput, defaults.registryDate);
              setValue(registryDueInput, defaults.registryDue);
              setValue(suggestionInput, defaults.suggestion);
              setValue(oilCostInput, defaults.oilCost);
              setValue(registryCostInput, defaults.registryCost);
            };
            const openForAdd = () => {
              resetToAddMode();
              open();
            };
            openButtons.forEach((button) => button.addEventListener("click", openForAdd));
            editButtons.forEach((button) => button.addEventListener("click", () => {
              resetToAddMode();
              if (title) title.textContent = `Sửa lịch kế hoạch #${button.dataset.logId || ""}`;
              if (submitButton) submitButton.textContent = "Lưu thay đổi";
              if (actionInput) actionInput.value = "update_next_log";
              if (recordIdInput) recordIdInput.value = button.dataset.logId || "";
              setValue(lastServiceInput, button.dataset.lastService || "");
              setValue(nextDueInput, button.dataset.nextDue || "");
              setValue(registryDateInput, button.dataset.registryDate || "");
              setValue(registryDueInput, button.dataset.registryDue || "");
              setValue(suggestionInput, button.dataset.suggestion || "");
              setValue(oilCostInput, button.dataset.oilCost || "");
              setValue(registryCostInput, button.dataset.registryCost || "");
              open();
            }));
            closeButtons.forEach((button) => button.addEventListener("click", close));
            modal.addEventListener("click", (event) => {
              if (event.target === modal) close();
            });
            document.addEventListener("keydown", (event) => {
              if (event.key === "Escape" && !modal.hidden) close();
            });
          });
        </script>
        {% endif %}

        <section class="metric-strip">
          <div class="metric-card brand">
            <span>Biển số</span>
            <strong>{{ selected }}</strong>
          </div>
          <div class="metric-card success">
            <span>Số lần bảo dưỡng</span>
            <strong>{{ periodic_count }}</strong>
          </div>
          <div class="metric-card danger">
            <span>Số lần sửa chữa</span>
            <strong>{{ repair_count }}</strong>
          </div>
          <div class="metric-card">
            <span>Tổng chi phí</span>
            <strong>{{ total_cost }}</strong>
          </div>
        </section>

        {% if alerts %}
          <section class="alerts">
            {% for alert in alerts %}
              <div class="alert {{ alert.level }}">{{ alert.text }}</div>
            {% endfor %}
          </section>
        {% endif %}

        <section class="panel lookup-overview">
          <div class="vehicle-compact">
            <div class="vehicle-identity">
              <div>
                <span>Thông tin xe</span>
                <strong>{{ car.get("Biển số", selected) }}</strong>
              </div>
              <span class="badge">{{ car.get("Trạng thái", "Chưa cập nhật") }}</span>
            </div>
            <div class="vehicle-compact-grid">
              <div class="vehicle-mini"><span>Loại xe</span><strong>{{ car.get("Loại xe", "Chưa cập nhật") }}</strong></div>
              <div class="vehicle-mini"><span>Năm sản xuất</span><strong>{{ year }}</strong></div>
              <div class="vehicle-mini"><span>Bảo dưỡng</span><strong>{{ periodic_count }} lần</strong></div>
              <div class="vehicle-mini"><span>Sửa chữa</span><strong>{{ repair_count }} lần</strong></div>
              <div class="vehicle-mini"><span>Hồ sơ</span><strong>{{ "Đầy đủ" if car else "Thiếu dữ liệu" }}</strong></div>
            </div>
          </div>

          <div class="plan-board">
            <div class="section-head">
              <h2>Lịch bảo dưỡng và đăng kiểm</h2>
              <div class="toolbar">
                {% if can_manage_history %}
                  <button class="btn primary" type="button" data-next-modal-open>Thêm</button>
                {% endif %}
              </div>
            </div>
            {% if next_item %}
                <div class="plan-timeline">
                  <div class="plan-step periodic">
                    <span>Lịch bảo dưỡng gần nhất</span>
                    <strong>{{ format_short_date(next_item.get("Lịch bảo dưỡng gần nhất")) }}</strong>
                  </div>
                  <div class="plan-step {{ maintenance_state.key }}">
                    <span>Lịch bảo dưỡng tiếp theo</span>
                    <strong>{{ format_short_date(next_item.get("Dự kiến lần tiếp theo")) }}</strong>
                    <small>{{ status_text(maintenance_state) }}</small>
                  </div>
                  <div class="plan-step registry">
                    <span>Ngày đăng kiểm</span>
                    <strong>{{ format_short_date(next_item.get("Ngày đăng kiểm")) }}</strong>
                  </div>
                  <div class="plan-step {{ registry_state.key }}">
                    <span>Hạn đăng kiểm đến</span>
                    <strong>{{ format_short_date(next_item.get("Hạn đăng kiểm đến")) }}</strong>
                    <small>{{ status_text(registry_state) }}</small>
                  </div>
                </div>
              <div class="plan-detail-grid">
                <div class="plan-note">
                  <span>Nội dung</span>
                  <strong>{{ next_item.get("Gợi ý nội dung") or "Chưa cập nhật" }}</strong>
                </div>
                <div class="plan-costs">
                  <div class="plan-cost oil"><span>Chi phí thay dầu</span><strong>{{ oil_cost }}</strong></div>
                  <div class="plan-cost registry"><span>Chi phí đăng kiểm</span><strong>{{ registry_cost }}</strong></div>
                </div>
              </div>
              {% if schedule_logs %}
                <div class="schedule-log-box">
                  <div class="section-head">
                    <h3>Lịch sử kế hoạch</h3>
                    <span class="badge">{{ schedule_logs|length }} bản gần nhất</span>
                  </div>
                  <div class="schedule-log-list">
                    {% for log in schedule_logs %}
                      <div class="schedule-log-row">
                        <div>
                          <strong>{{ log.saved_at }}</strong>
                          <span>{{ format_short_date(log.last_service) }} → {{ format_short_date(log.next_due) }}</span>
                        </div>
                        <div>
                          <strong>{{ log.oil_cost }}</strong>
                          <span>{{ log.registry_cost }}</span>
                        </div>
                        <div class="schedule-log-note">{{ log.suggestion }}</div>
                        <div class="schedule-log-actions">
                          {% if can_manage_history %}
                            <button
                              class="btn primary"
                              type="button"
                              data-next-log-edit
                              data-log-id="{{ log.id }}"
                              data-last-service="{{ log.last_service_raw }}"
                              data-next-due="{{ log.next_due_raw }}"
                              data-suggestion="{{ log.suggestion_raw }}"
                              data-registry-date="{{ log.registry_date_raw }}"
                              data-registry-due="{{ log.registry_due_raw }}"
                              data-oil-cost="{{ log.oil_cost_raw }}"
                              data-registry-cost="{{ log.registry_cost_raw }}"
                              data-saved-at="{{ log.saved_at }}"
                            >Sửa</button>
                            <form method="post" action="{{ url_for('admin_data') }}" onsubmit="return confirm('Xóa dòng lịch kế hoạch này?')">
                              <input type="hidden" name="action" value="delete_next_log">
                              <input type="hidden" name="plate" value="{{ selected }}">
                              <input type="hidden" name="record_id" value="{{ log.id }}">
                              <input type="hidden" name="return_to" value="lookup">
                              <input type="hidden" name="return_plate" value="{{ selected }}">
                              <button class="btn danger" type="submit">Xóa</button>
                            </form>
                          {% endif %}
                        </div>
                      </div>
                    {% endfor %}
                  </div>
                </div>
              {% endif %}
            {% else %}
              <div class="empty">Chưa có lịch bảo dưỡng tiếp theo.</div>
            {% endif %}
          </div>
        </section>

        {% if can_manage_history %}
          <div class="inline-modal-backdrop" id="next-service-modal" hidden>
            <div class="inline-modal" role="dialog" aria-modal="true" aria-labelledby="next-service-modal-title">
              <div class="inline-modal-head">
                <h3 id="next-service-modal-title" data-next-modal-title>Thêm lịch bảo dưỡng và đăng kiểm</h3>
                <button class="dashboard-modal-close" type="button" data-next-modal-close aria-label="Đóng">×</button>
              </div>
              <div class="inline-modal-body">
                <form method="post" action="{{ url_for('admin_data') }}">
                  <input type="hidden" name="action" value="save_next" id="next-modal-action">
                  <input type="hidden" name="record_id" value="" id="next-modal-record-id">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <input type="hidden" name="return_to" value="lookup">
                  <input type="hidden" name="return_plate" value="{{ selected }}">
                  <div class="inline-modal-grid">
                    <div class="form-row">
                      <div class="field">
                        <label for="modal-last-service">Lịch bảo dưỡng gần nhất</label>
                        <input id="modal-last-service" name="last_service" type="date" value="{{ date_input_value(next_item.get('Lịch bảo dưỡng gần nhất', '')) }}">
                      </div>
                      <div class="field">
                        <label for="modal-next-due">Dự kiến lần tiếp theo</label>
                        <input id="modal-next-due" name="next_due" type="date" value="{{ date_input_value(next_item.get('Dự kiến lần tiếp theo', '')) }}">
                      </div>
                    </div>
                    <div class="form-row">
                      <div class="field">
                        <label for="modal-registry-date">Ngày đăng kiểm</label>
                        <input id="modal-registry-date" name="registry_date" type="date" value="{{ date_input_value(next_item.get('Ngày đăng kiểm', today)) if next_item.get('Ngày đăng kiểm') else today }}">
                      </div>
                      <div class="field">
                        <label for="modal-registry-due">Hạn đăng kiểm đến</label>
                        <input id="modal-registry-due" name="registry_due" type="date" value="{{ date_input_value(next_item.get('Hạn đăng kiểm đến', '')) }}">
                      </div>
                    </div>
                    <div class="field">
                      <label for="modal-suggestion">Gợi ý nội dung</label>
                      <textarea id="modal-suggestion" name="suggestion" placeholder="VD: thay dầu, kiểm tra phanh...">{{ next_item.get('Gợi ý nội dung', '') }}</textarea>
                    </div>
                    <div class="form-row">
                      <div class="field">
                        <label for="modal-oil-cost">Chi phí thay dầu</label>
                        <input id="modal-oil-cost" class="money-input" name="oil_cost" value="{{ format_money_input(next_item.get('Chi phí thay dầu', '')) }}" inputmode="numeric" autocomplete="off" placeholder="VD: 1.500.000" pattern="[0-9]+([.][0-9]{3})*" title="Vui lòng nhập số tiền, ví dụ 1.500.000" required>
                      </div>
                      <div class="field">
                        <label for="modal-registry-cost">Chi phí đăng kiểm</label>
                        <input id="modal-registry-cost" class="money-input" name="registry_cost" value="{{ format_money_input(next_item.get('Chi phí đăng kiểm', '')) }}" inputmode="numeric" autocomplete="off" placeholder="VD: 2.500.000" pattern="[0-9]+([.][0-9]{3})*" title="Vui lòng nhập số tiền, ví dụ 2.500.000" required>
                      </div>
                    </div>
                    <div class="inline-modal-actions">
                      <button class="btn" type="button" data-next-modal-close>Hủy</button>
                      <button class="btn primary" type="submit" id="next-modal-submit">Lưu lịch tiếp theo</button>
                    </div>
                  </div>
                </form>
              </div>
            </div>
          </div>
        {% endif %}

        <section class="panel" style="margin-top:12px">
          <div class="section-head">
            <div>
              <h2>Lịch sử bảo dưỡng</h2>
              <p class="subtle">Danh sách các lần bảo dưỡng đã ghi nhận</p>
            </div>
            <div class="toolbar">
              {% if can_manage_history %}
                <button class="btn" type="button" data-history-add>Thêm mới</button>
              {% endif %}
              <a class="btn primary" href="{{ url_for('export_excel', plate=selected) }}">Xuất Excel</a>
              <span class="badge">{{ history_rows|length }} bản ghi</span>
            </div>
          </div>
          {% if history_rows %}
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Ngày</th>
                    <th>Loại</th>
                    <th>Nội dung</th>
                    <th>Chi phí</th>
                    {% if can_manage_history %}<th>Thao tác</th>{% endif %}
                  </tr>
                </thead>
                <tbody>
                  {% for row in history_rows %}
                    <tr>
                      <td data-label="Ngày">{{ row.date }}</td>
                      <td data-label="Loại"><span class="cost-type-pill {{ row.cost_type }}">{{ row.cost_type_label }}</span></td>
                      <td class="history-content" data-label="Nội dung">{{ row.content }}</td>
                      <td class="cost" data-label="Chi phí">{{ row.cost }}</td>
                      {% if can_manage_history %}
                        <td data-label="Thao tác">
                          <div class="history-actions">
                            <button
                              class="btn primary"
                              type="button"
                              data-history-edit
                              data-record-id="{{ row.id }}"
                              data-service-date="{{ row.date_raw }}"
                              data-cost-type="{{ row.cost_type }}"
                              data-cost="{{ row.cost_input }}"
                              data-content="{{ row.content_raw }}"
                            >Sửa</button>
                            <form method="post" action="{{ url_for('admin_data') }}" onsubmit="return confirm('Xóa bản ghi lịch sử này?')">
                              <input type="hidden" name="action" value="delete_history">
                              <input type="hidden" name="plate" value="{{ selected }}">
                              <input type="hidden" name="record_id" value="{{ row.id }}">
                              <input type="hidden" name="return_to" value="lookup">
                              <input type="hidden" name="return_plate" value="{{ selected }}">
                              <button class="btn danger" type="submit">Xóa</button>
                            </form>
                          </div>
                        </td>
                      {% endif %}
                    </tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
          {% else %}
            <div class="empty">Chưa có lịch sử bảo dưỡng.</div>
          {% endif %}
        </section>
        {% if can_manage_history %}
          <div class="inline-modal-backdrop" id="history-record-modal" hidden>
            <div class="inline-modal" role="dialog" aria-modal="true" aria-labelledby="history-record-modal-title">
              <div class="inline-modal-head">
                <h3 id="history-record-modal-title" data-history-modal-title>Sửa lịch sử bảo dưỡng</h3>
                <button class="dashboard-modal-close" type="button" data-history-modal-close aria-label="Đóng">×</button>
              </div>
              <div class="inline-modal-body">
                <form method="post" action="{{ url_for('admin_data') }}">
                  <input type="hidden" name="action" value="update_history" id="history-modal-action">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <input type="hidden" name="record_id" value="" id="history-modal-record-id">
                  <input type="hidden" name="return_to" value="lookup">
                  <input type="hidden" name="return_plate" value="{{ selected }}">
                  <div class="inline-modal-grid">
                    <div class="form-row">
                      <div class="field">
                        <label for="history-modal-date">Ngày</label>
                        <input id="history-modal-date" name="service_date" type="date" value="{{ today }}" required>
                      </div>
                      <div class="field">
                        <label for="history-modal-cost">Chi phí</label>
                        <input id="history-modal-cost" class="money-input" name="cost" inputmode="numeric" autocomplete="off" placeholder="VD: 1.500.000" pattern="[0-9]+([.][0-9]{3})*" title="Vui lòng nhập số tiền, ví dụ 1.500.000" required>
                      </div>
                    </div>
                    <div class="field">
                      <label for="history-modal-type">Loại chi phí</label>
                      <select id="history-modal-type" name="cost_type">
                        <option value="repair">Chi phí sửa chữa</option>
                        <option value="periodic">Chi phí bảo dưỡng định kỳ</option>
                      </select>
                    </div>
                    <div class="field">
                      <label for="history-modal-content">Nội dung</label>
                      <textarea id="history-modal-content" name="content" placeholder="Nhập nội dung bảo dưỡng" required></textarea>
                    </div>
                    <div class="inline-modal-actions">
                      <button class="btn" type="button" data-history-modal-close>Hủy</button>
                      <button class="btn primary" type="submit" id="history-modal-submit">Lưu thay đổi</button>
                    </div>
                  </div>
                </form>
              </div>
            </div>
          </div>
          <script>
            (() => {
              const modal = document.getElementById("history-record-modal");
              if (!modal) return;
              const title = modal.querySelector("[data-history-modal-title]");
              const actionInput = modal.querySelector("#history-modal-action");
              const recordIdInput = modal.querySelector("#history-modal-record-id");
              const dateInput = modal.querySelector("#history-modal-date");
              const costInput = modal.querySelector("#history-modal-cost");
              const typeInput = modal.querySelector("#history-modal-type");
              const contentInput = modal.querySelector("#history-modal-content");
              const submitButton = modal.querySelector("#history-modal-submit");
              const addButton = document.querySelector("[data-history-add]");
              const editButtons = document.querySelectorAll("[data-history-edit]");
              const closeButtons = modal.querySelectorAll("[data-history-modal-close]");
              const cleanMoney = (value) => String(value || "").replace(/[^\d]/g, "");
              const formatMoney = (value) => {
                const digits = cleanMoney(value);
                return digits ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".") : "";
              };
              const open = () => {
                modal.hidden = false;
                requestAnimationFrame(() => modal.classList.add("open"));
              };
              const close = () => {
                modal.classList.remove("open");
                modal.hidden = true;
              };
              const openForAdd = () => {
                if (title) title.textContent = "Thêm lịch sử bảo dưỡng";
                if (actionInput) actionInput.value = "add_history";
                if (recordIdInput) recordIdInput.value = "";
                if (dateInput) dateInput.value = "{{ today }}";
                if (costInput) costInput.value = "";
                if (typeInput) typeInput.value = "repair";
                if (contentInput) contentInput.value = "";
                if (submitButton) submitButton.textContent = "Thêm bản ghi";
                open();
              };
              editButtons.forEach((button) => {
                button.addEventListener("click", () => {
                  if (title) title.textContent = "Sửa lịch sử bảo dưỡng";
                  if (actionInput) actionInput.value = "update_history";
                  recordIdInput.value = button.dataset.recordId || "";
                  dateInput.value = button.dataset.serviceDate || "";
                  costInput.value = formatMoney(button.dataset.cost || "");
                  typeInput.value = button.dataset.costType || "repair";
                  contentInput.value = button.dataset.content || "";
                  if (submitButton) submitButton.textContent = "Lưu thay đổi";
                  open();
                });
              });
              if (addButton) addButton.addEventListener("click", openForAdd);
              costInput.addEventListener("input", () => {
                const formatted = formatMoney(costInput.value);
                costInput.value = formatted;
                costInput.setSelectionRange(formatted.length, formatted.length);
              });
              closeButtons.forEach((button) => button.addEventListener("click", close));
              modal.addEventListener("click", (event) => {
                if (event.target === modal) close();
              });
              document.addEventListener("keydown", (event) => {
                if (event.key === "Escape" && !modal.hidden) close();
              });
            })();
          </script>
        {% endif %}
        """,
        plates=plates,
        plate_labels=plate_labels,
        selected=selected,
        car=car,
        year=normalize_year(car.get("Năm sản xuất")),
        next_item=next_item,
        maintenance_state=maintenance_state,
        registry_state=registry_state,
        status_text=status_text,
        format_short_date=format_short_date,
        oil_cost=format_vnd(next_item.get("Chi phí thay dầu")),
        registry_cost=format_vnd(next_item.get("Chi phí đăng kiểm")),
        history_rows=history_rows,
        repair_count=repair_count,
        periodic_count=periodic_count,
        schedule_logs=schedule_logs,
        total_cost=format_vnd(total_cost),
        history_query=history_query,
        date_from_raw=date_from_raw,
        date_to_raw=date_to_raw,
        alerts=alerts,
        can_manage_history=is_admin(),
        date_input_value=date_input_value,
        format_money_input=format_money_input,
    )
    return page(content)


@app.route("/lookup")
def lookup_legacy():
    return redirect(url_for("detail"))


@app.route("/admin/data", methods=["GET", "POST"])
@login_required
def admin_data():
    if not is_admin():
        return Response("Forbidden", status=403)

    if request.method == "POST":
        action = request.form.get("action")
        plate = normalize_plate(request.form.get("plate", ""))
        return_to = request.form.get("return_to", "").strip()
        return_plate = normalize_plate(request.form.get("return_plate", ""))
        redirect_tab = "vehicles"
        if action == "save_vehicle":
            redirect_tab = "vehicles"
            if upsert_vehicle(
                plate,
                request.form.get("car_type", ""),
                request.form.get("manufacture_year", ""),
                request.form.get("status", ""),
            ):
                flash(f"Đã lưu xe {plate}.", "success")
                if request.form.get("form_mode") != "edit":
                    plate = ""
            else:
                flash("Vui lòng nhập biển số xe.", "error")
        elif action == "save_next":
            redirect_tab = "maintenance"
            if upsert_next_service(
                plate,
                request.form.get("last_service", ""),
                request.form.get("next_due", ""),
                request.form.get("suggestion", ""),
                request.form.get("registry_date", ""),
                request.form.get("registry_due", ""),
                request.form.get("oil_cost", ""),
                request.form.get("registry_cost", ""),
            ):
                flash(f"Đã cập nhật lịch tiếp theo cho xe {plate}.", "success")
            else:
                flash("Vui lòng chọn xe trước khi cập nhật lịch.", "error")
        elif action == "update_next_log":
            redirect_tab = "maintenance"
            record_id = request.form.get("record_id", "")
            if update_next_service_log(
                record_id,
                plate,
                request.form.get("last_service", ""),
                request.form.get("next_due", ""),
                request.form.get("suggestion", ""),
                request.form.get("registry_date", ""),
                request.form.get("registry_due", ""),
                request.form.get("oil_cost", ""),
                request.form.get("registry_cost", ""),
            ):
                flash("Đã lưu dòng lịch kế hoạch.", "success")
            else:
                flash("Không tìm thấy dòng lịch kế hoạch cần sửa.", "error")
        elif action == "delete_next_log":
            redirect_tab = "maintenance"
            record_id = request.form.get("record_id", "")
            if delete_next_service_log(record_id, plate):
                flash("Đã xóa dòng lịch kế hoạch.", "success")
            else:
                flash("Không tìm thấy dòng lịch kế hoạch cần xóa.", "error")
        elif action == "add_history":
            redirect_tab = "maintenance"
            if add_history_record(
                plate,
                request.form.get("service_date", ""),
                request.form.get("content", ""),
                request.form.get("cost", ""),
                request.form.get("cost_type", COST_TYPE_REPAIR),
            ):
                flash(f"Đã thêm lịch sử bảo dưỡng cho xe {plate}.", "success")
            else:
                flash("Vui lòng chọn xe trước khi thêm lịch sử.", "error")
        elif action == "update_history":
            redirect_tab = "maintenance"
            record_id = request.form.get("record_id", "")
            if update_history_record(
                record_id,
                plate,
                request.form.get("service_date", ""),
                request.form.get("content", ""),
                request.form.get("cost", ""),
                request.form.get("cost_type", COST_TYPE_REPAIR),
            ):
                flash("Đã cập nhật bản ghi lịch sử.", "success")
            else:
                flash("Không tìm thấy bản ghi lịch sử cần sửa.", "error")
        elif action == "delete_history":
            redirect_tab = "maintenance"
            record_id = request.form.get("record_id", "")
            if record_id and delete_history_record(record_id):
                flash("Đã xóa bản ghi lịch sử.", "success")
            else:
                flash("Không tìm thấy bản ghi lịch sử cần xóa.", "error")
        elif action == "delete_next":
            redirect_tab = "maintenance"
            if delete_next_service(plate):
                flash(f"Da xoa lich tiep theo cho xe {plate}.", "success")
            else:
                flash("Khong tim thay lich tiep theo can xoa.", "error")
        elif action == "deactivate_vehicle":
            redirect_tab = "vehicles"
            if deactivate_vehicle(plate):
                flash(f"Đã chuyển xe {plate} sang trạng thái Ngừng hoạt động.", "success")
                plate = ""
            else:
                flash("Không tìm thấy xe cần cập nhật.", "error")
        page_arg = request.form.get("page", "").strip()
        if return_to == "lookup":
            target_plate = return_plate or plate
            return redirect(url_for("detail", plate=target_plate) if target_plate else url_for("detail"))
        if redirect_tab == "vehicles":
            return redirect(url_for("admin_data", tab=redirect_tab, page=page_arg) if page_arg else url_for("admin_data", tab=redirect_tab))
        return redirect(url_for("admin_data", tab=redirect_tab, plate=plate) if plate else url_for("admin_data", tab=redirect_tab))

    data = load_data(["xe", "history", "next"])
    tab = request.args.get("tab", "vehicles")
    if tab not in ("vehicles", "maintenance"):
        tab = "vehicles"
    all_plates = safe_unique(data["xe"], "Biển số")
    active_vehicles = active_vehicle_df(data["xe"])
    active_plates = safe_unique(active_vehicles, "Biển số")
    plates = active_plates if tab == "maintenance" else all_plates
    plate_labels = plate_label_map(active_vehicles if tab == "maintenance" else data["xe"])
    requested_plate = normalize_plate(request.args.get("plate", ""))
    vehicle_mode = request.args.get("mode", "").strip().lower()
    new_next_mode = request.args.get("new_next", "").strip() == "1"
    new_history_mode = request.args.get("new_history", "").strip() == "1"
    if vehicle_mode not in ("add", "edit"):
        vehicle_mode = ""
    if tab == "maintenance":
        selected = requested_plate or (plates[0] if plates else "")
    elif vehicle_mode == "edit":
        selected = requested_plate
    else:
        selected = ""
    if selected and selected not in plates:
        selected = plates[0] if tab == "maintenance" and plates else ""
    if tab == "vehicles" and vehicle_mode == "edit" and not selected:
        vehicle_mode = ""

    car = {}
    next_item = {}
    history_rows = []
    selected_history = {}
    history_edit_id = request.args.get("history_id", "").strip()
    if selected:
        car_row = data["xe"][data["xe"]["Biển số"].astype(str) == selected]
        car = car_row.iloc[0].to_dict() if not car_row.empty else {"Biển số": selected}
        next_row = data["next"][data["next"]["Biển số"].astype(str) == selected]
        next_item = next_row.iloc[0].to_dict() if not next_row.empty else {"Biển số": selected}
        if tab == "maintenance" and new_next_mode:
            next_item = {"Biển số": selected}
        history_view = data["history"][data["history"]["Biển số"].astype(str) == selected].copy()
        history_rows = build_history_rows(history_view)
        if new_history_mode:
            selected_history = {}
        elif history_edit_id:
            edit_row = history_view[history_view["ID"].astype(str) == history_edit_id]
            if not edit_row.empty:
                selected_history = edit_row.iloc[0].to_dict()
    car_status = normalize_vehicle_status(car.get("Trạng thái", ""))

    vehicle_rows = []
    for _, row in data["xe"].iterrows():
        plate_value = str(row.get("Biển số", "")).strip()
        if not plate_value:
            continue
        next_row = data["next"][data["next"]["Biển số"].astype(str) == plate_value]
        next_due = next_row.iloc[0].get("Dự kiến lần tiếp theo", "") if not next_row.empty else ""
        vehicle_rows.append(
            {
                "plate": plate_value,
                "car_type": row.get("Loại xe", ""),
                "year": normalize_year(row.get("Năm sản xuất")),
                "status": normalize_vehicle_status(row.get("Trạng thái", "")),
                "next_due": next_due or "Chưa cập nhật",
                "selected": plate_value == selected,
            }
        )

    vehicle_query = request.args.get("q", "").strip()
    vehicle_all_total = len(vehicle_rows)
    if vehicle_query:
        normalized_query = vehicle_query.lower()
        vehicle_rows = [
            row
            for row in vehicle_rows
            if normalized_query in " ".join(
                str(row.get(key, "")) for key in ("plate", "car_type", "year", "status", "next_due")
            ).lower()
        ]

    vehicle_per_page = 10
    vehicle_total = len(vehicle_rows)
    vehicle_total_pages = max(1, (vehicle_total + vehicle_per_page - 1) // vehicle_per_page)
    page_raw = request.args.get("page", "").strip()
    try:
        vehicle_page = int(page_raw) if page_raw else 0
    except ValueError:
        vehicle_page = 0
    if not vehicle_page and selected:
        selected_index = next((index for index, row in enumerate(vehicle_rows) if row["plate"] == selected), 0)
        vehicle_page = (selected_index // vehicle_per_page) + 1
    if vehicle_page < 1:
        vehicle_page = 1
    if vehicle_page > vehicle_total_pages:
        vehicle_page = vehicle_total_pages
    vehicle_start = (vehicle_page - 1) * vehicle_per_page
    vehicle_end = min(vehicle_start + vehicle_per_page, vehicle_total)
    vehicle_page_rows = vehicle_rows[vehicle_start:vehicle_end]

    content = render_template_string(
        r"""
        <section class="hero">
          <div>
            <h1>Quản lý dữ liệu xe</h1>
          </div>
        </section>

        <div class="admin-shell">
          <aside class="panel admin-sidebar">
            <div>
              <h2>Quản trị</h2>
            </div>
            <nav class="admin-menu">
              <a class="{{ 'active' if tab == 'vehicles' else '' }}" href="{{ url_for('admin_data', tab='vehicles') }}">
                Quản lý xe
              </a>
              <a href="{{ url_for('admin', view='access') }}">
                Quản lý mã truy cập
              </a>
              <a href="{{ url_for('admin', view='audit') }}">
                Lịch sử truy cập
              </a>
            </nav>
          </aside>
          <div class="admin-content">

        {% if tab == 'vehicles' %}
        <div class="admin-layout no-form">
          {% if vehicle_mode %}
          <div class="vehicle-modal-backdrop">
          <section class="panel vehicle-modal">
            <div class="section-head">
              <h2>{{ "Sửa thông tin xe" if vehicle_mode == "edit" else "Thêm xe mới" }}</h2>
              <div class="toolbar">
                <span class="badge">{{ selected if vehicle_mode == "edit" else "Mới" }}</span>
                <a class="vehicle-modal-close" href="{{ url_for('admin_data', tab='vehicles', page=vehicle_page, q=vehicle_query) }}" aria-label="Đóng">&times;</a>
              </div>
            </div>
            <form method="post">
              <input type="hidden" name="action" value="save_vehicle">
              <input type="hidden" name="form_mode" value="{{ 'edit' if vehicle_mode == 'edit' else 'create' }}">
              <input type="hidden" name="page" value="{{ vehicle_page }}">
              <div class="field">
                <label for="plate">Biển số</label>
                <input id="plate" name="plate" value="{{ car.get("Biển số", selected) }}" placeholder="VD: 30A12345" required>
              </div>
              <div class="field">
                <label for="car-type">Loại xe</label>
                <input id="car-type" name="car_type" value="{{ car.get("Loại xe", "") }}" placeholder="VD: Toyota Camry">
              </div>
              <div class="form-row">
                <div class="field">
                  <label for="manufacture-year">Năm sản xuất</label>
                  <input id="manufacture-year" name="manufacture_year" type="date" value="{{ date_input_value(car.get("Năm sản xuất", "")) }}">
                </div>
                <div class="field">
                  <label for="status">Trạng thái</label>
                  <select id="status" name="status">
                    <option value="Đang hoạt động" {{ 'selected' if car_status == "Đang hoạt động" else '' }}>Đang hoạt động</option>
                    <option value="Ngừng hoạt động" {{ 'selected' if car_status == "Ngừng hoạt động" else '' }}>Ngừng hoạt động</option>
                  </select>
                </div>
              </div>
              <div class="vehicle-modal-actions">
                <a class="btn" href="{{ url_for('admin_data', tab='vehicles', page=vehicle_page, q=vehicle_query) }}">Hủy</a>
                <button class="btn primary" type="submit">Lưu thông tin xe</button>
              </div>
            </form>
            {% if vehicle_mode == "edit" and selected %}
              <form method="post" style="margin-top:10px" onsubmit="return confirm('Chuyển xe {{ selected }} sang trạng thái Ngừng hoạt động?')">
                <input type="hidden" name="action" value="deactivate_vehicle">
                <input type="hidden" name="plate" value="{{ selected }}">
                <input type="hidden" name="page" value="{{ vehicle_page }}">
                <button class="btn danger full" type="submit">Ngừng hoạt động</button>
              </form>
            {% endif %}
          </section>
          </div>
          {% endif %}

          <section class="panel">
            <div class="section-head">
              <h2>Danh sách xe</h2>
            </div>
            <div class="vehicle-controls">
              <form class="vehicle-search" method="get">
                <input type="hidden" name="tab" value="vehicles">
                <input name="q" value="{{ vehicle_query }}" placeholder="Tìm biển số, loại xe, trạng thái">
              </form>
              <span class="badge">{{ vehicle_start + 1 if vehicle_total else 0 }}-{{ vehicle_end }}/{{ vehicle_total }} xe{% if vehicle_query %} / {{ vehicle_all_total }}{% endif %}</span>
              <a class="btn primary" href="{{ url_for('admin_data', tab='vehicles', mode='add', page=vehicle_page, q=vehicle_query) }}">Thêm xe</a>
            </div>
            {% if vehicle_rows %}
              <div class="table-wrap admin-data-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Biển số</th>
                      <th>Loại xe</th>
                      <th>Năm</th>
                      <th>Trạng thái</th>
                      <th>Lịch tới</th>
                      <th>Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {% for row in vehicle_page_rows %}
                      <tr class="{{ 'access-active' if row.selected else '' }}">
                        <td class="code" data-label="Biển số">{{ row.plate }}</td>
                        <td data-label="Loại xe">{{ row.car_type or "Chưa cập nhật" }}</td>
                        <td data-label="Năm">{{ row.year }}</td>
                        <td data-label="Trạng thái">{{ row.status or "Chưa cập nhật" }}</td>
                        <td data-label="Lịch tới">{{ row.next_due }}</td>
                        <td data-label="Thao tác"><a class="btn primary" href="{{ url_for('admin_data', tab='vehicles', mode='edit', plate=row.plate, page=vehicle_page, q=vehicle_query) }}">Sửa</a></td>
                      </tr>
                    {% endfor %}
                  </tbody>
                </table>
              </div>
              {% if vehicle_total_pages > 1 %}
                <div class="pagination">
                  <span class="subtle">Trang {{ vehicle_page }}/{{ vehicle_total_pages }}</span>
                  <div class="pagination-pages">
                    <a data-preserve-scroll class="btn {{ 'disabled' if vehicle_page <= 1 else '' }}" href="{{ url_for('admin_data', tab='vehicles', mode=vehicle_mode, plate=selected, page=vehicle_page - 1, q=vehicle_query) if vehicle_mode == 'edit' else url_for('admin_data', tab='vehicles', mode=vehicle_mode, page=vehicle_page - 1, q=vehicle_query) }}">Trước</a>
                    {% for p in range(1, vehicle_total_pages + 1) %}
                      <a data-preserve-scroll class="btn {{ 'active' if p == vehicle_page else '' }}" href="{{ url_for('admin_data', tab='vehicles', mode=vehicle_mode, plate=selected, page=p, q=vehicle_query) if vehicle_mode == 'edit' else url_for('admin_data', tab='vehicles', mode=vehicle_mode, page=p, q=vehicle_query) }}">{{ p }}</a>
                    {% endfor %}
                    <a data-preserve-scroll class="btn {{ 'disabled' if vehicle_page >= vehicle_total_pages else '' }}" href="{{ url_for('admin_data', tab='vehicles', mode=vehicle_mode, plate=selected, page=vehicle_page + 1, q=vehicle_query) if vehicle_mode == 'edit' else url_for('admin_data', tab='vehicles', mode=vehicle_mode, page=vehicle_page + 1, q=vehicle_query) }}">Sau</a>
                  </div>
                </div>
              {% endif %}
            {% else %}
              <div class="empty">{{ "Không tìm thấy xe phù hợp." if vehicle_query else "Chưa có xe nào. Bấm Thêm xe để tạo xe đầu tiên." }}</div>
            {% endif %}
            <script>
              (() => {
                const key = "adminVehicleScrollY";
                const saved = sessionStorage.getItem(key);
                if (saved !== null) {
                  sessionStorage.removeItem(key);
                  requestAnimationFrame(() => window.scrollTo(0, Number(saved) || 0));
                }
                document.querySelectorAll("[data-preserve-scroll]").forEach((link) => {
                  link.addEventListener("click", () => {
                    sessionStorage.setItem(key, String(window.scrollY));
                  });
                });
              })();
            </script>
          </section>
        </div>

        {% endif %}

        {% if tab == 'maintenance' %}
          <section class="panel admin-vehicle-picker">
            <div class="section-head">
              <div>
                <h2>Chọn xe</h2>
              </div>
              <span class="badge">{{ selected or "Chưa chọn" }}</span>
            </div>
            {% if vehicle_rows %}
              <form method="get" class="toolbar" id="admin-plate-picker-form">
                <input type="hidden" name="tab" value="maintenance">
                <input type="hidden" name="plate" id="admin-plate-value" value="{{ selected }}">
                <div class="combo" id="admin-plate-combo">
                  <input
                    id="admin-plate-search"
                    value="{{ plate_labels.get(selected, selected) }}"
                    placeholder="Tìm biển số hoặc loại xe"
                    autocomplete="off"
                  >
                  <button class="combo-toggle" type="button" aria-label="Mở danh sách xe">▼</button>
                  <div class="combo-menu" id="admin-plate-options">
                    {% for plate in plates %}
                      <button
                        class="combo-option"
                        type="button"
                        data-plate="{{ plate }}"
                        data-label="{{ plate_labels.get(plate, plate) }}"
                      >{{ plate_labels.get(plate, plate) }}</button>
                    {% endfor %}
                  </div>
                </div>
              </form>
              <script>
                (() => {
                  const form = document.getElementById("admin-plate-picker-form");
                  const combo = document.getElementById("admin-plate-combo");
                  const search = document.getElementById("admin-plate-search");
                  const value = document.getElementById("admin-plate-value");
                  const toggle = combo?.querySelector(".combo-toggle");
                  const options = Array.from(document.querySelectorAll("#admin-plate-options .combo-option"));
                  if (!form || !combo || !search || !value || !toggle) return;

                  const openMenu = () => combo.classList.add("open");
                  const closeMenu = () => combo.classList.remove("open");
                  const filterOptions = (query = "") => {
                    const normalized = query.trim().toLowerCase();
                    options.forEach((option) => {
                      const label = option.dataset.label.toLowerCase();
                      const plate = option.dataset.plate.toLowerCase();
                      option.style.display = !normalized || label.includes(normalized) || plate.includes(normalized) ? "" : "none";
                    });
                  };
                  const syncPlate = () => {
                    const typed = search.value.trim();
                    const lowerTyped = typed.toLowerCase();
                    const match = options.find((option) =>
                      option.dataset.label === typed ||
                      option.dataset.plate === typed ||
                      option.dataset.label.toLowerCase() === lowerTyped ||
                      option.dataset.plate.toLowerCase() === lowerTyped
                    );
                    if (!match) return false;
                    value.value = match.dataset.plate;
                    search.value = match.dataset.label;
                    return true;
                  };

                  toggle.addEventListener("click", () => {
                    filterOptions("");
                    openMenu();
                    search.focus();
                  });
                  search.addEventListener("focus", () => {
                    search.select();
                    filterOptions("");
                    openMenu();
                  });
                  search.addEventListener("input", () => {
                    filterOptions(search.value);
                    openMenu();
                  });
                  options.forEach((option) => {
                    option.addEventListener("click", () => {
                      value.value = option.dataset.plate;
                      search.value = option.dataset.label;
                      closeMenu();
                      form.submit();
                    });
                  });
                  form.addEventListener("submit", (event) => {
                    if (!syncPlate()) event.preventDefault();
                  });
                  document.addEventListener("click", (event) => {
                    if (!combo.contains(event.target)) closeMenu();
                  });
                })();
              </script>
            {% else %}
              <div class="empty">Chưa có xe nào. Vào menu Quản lý xe để thêm xe trước.</div>
            {% endif %}
          </section>

        {% if selected %}
          <div class="grid" style="margin-top:12px">
            <section class="panel maintenance-card">
              <div class="section-head">
                <h2>Lịch bảo dưỡng tiếp theo</h2>
                <span class="badge">{{ selected }}</span>
              </div>
              <form method="post" class="maintenance-form">
                <input type="hidden" name="action" value="save_next">
                <input type="hidden" name="plate" value="{{ selected }}">
                <div class="form-row">
                  <div class="field">
                    <label for="last-service">Lịch bảo dưỡng gần nhất</label>
                    <input id="last-service" name="last_service" type="date" value="{{ date_input_value(next_item.get("Lịch bảo dưỡng gần nhất", "")) }}">
                  </div>
                  <div class="field">
                    <label for="next-due">Dự kiến lần tiếp theo</label>
                    <input id="next-due" name="next_due" type="date" value="{{ date_input_value(next_item.get("Dự kiến lần tiếp theo", "")) }}">
                  </div>
                  <div class="field">
                    <label for="registry-date">Ngày đăng kiểm</label>
                    <input id="registry-date" name="registry_date" type="date" value="{{ date_input_value(next_item.get("Ngày đăng kiểm", today)) if next_item.get("Ngày đăng kiểm") else today }}">
                  </div>
                </div>
                <div class="form-row">
                  <div class="field">
                    <label for="registry-due">Hạn đăng kiểm đến</label>
                    <input id="registry-due" name="registry_due" type="date" value="{{ date_input_value(next_item.get("Hạn đăng kiểm đến", "")) }}">
                  </div>
                </div>
                <div class="field">
                  <label for="suggestion">Gợi ý nội dung</label>
                  <textarea id="suggestion" name="suggestion" placeholder="VD: thay dầu, kiểm tra phanh...">{{ next_item.get("Gợi ý nội dung", "") }}</textarea>
                </div>
                <div class="form-row">
                  <div class="field">
                    <label for="oil-cost">Chi phí thay dầu</label>
                    <input id="oil-cost" class="money-input" name="oil_cost" value="{{ format_money_input(next_item.get("Chi phí thay dầu", "")) }}" inputmode="numeric" autocomplete="off" placeholder="VD: 1.500.000" pattern="[0-9]+([.][0-9]{3})*" title="Vui lòng nhập số tiền, ví dụ 1.500.000" required>
                  </div>
                  <div class="field">
                    <label for="registry-cost">Chi phí đăng kiểm</label>
                    <input id="registry-cost" class="money-input" name="registry_cost" value="{{ format_money_input(next_item.get("Chi phí đăng kiểm", "")) }}" inputmode="numeric" autocomplete="off" placeholder="VD: 2.500.000" pattern="[0-9]+([.][0-9]{3})*" title="Vui lòng nhập số tiền, ví dụ 2.500.000" required>
                  </div>
                </div>
                <button class="btn primary full" type="submit">Lưu lịch tiếp theo</button>
              </form>
              {% if next_item %}
                <form method="post" class="maintenance-form" onsubmit="return confirm('Xóa lịch bảo dưỡng/đăng kiểm tiếp theo của xe này?')">
                  <input type="hidden" name="action" value="delete_next">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <button class="btn danger full" type="submit">Xóa lịch tiếp theo</button>
                </form>
              {% endif %}
            </section>

            <section class="panel maintenance-card">
              <div class="section-head">
                <h2>{{ "Sửa lịch sử bảo dưỡng" if selected_history else "Thêm lịch sử bảo dưỡng" }}</h2>
                <span class="badge">{{ selected }}</span>
              </div>
              <form method="post" class="maintenance-form">
                <input type="hidden" name="action" value="{{ "update_history" if selected_history else "add_history" }}">
                <input type="hidden" name="plate" value="{{ selected }}">
                {% if selected_history %}
                  <input type="hidden" name="record_id" value="{{ selected_history.get("ID", "") }}">
                {% endif %}
                <div class="form-row">
                  <div class="field">
                    <label for="service-date">Ngày</label>
                    <input id="service-date" name="service_date" type="date" value="{{ date_input_value(selected_history.get("Ngày", "")) if selected_history else today }}" required>
                  </div>
                  <div class="field">
                    <label for="history-cost">Chi phí</label>
                    <input id="history-cost" class="money-input" name="cost" value="{{ format_money_input(selected_history.get("Chi phí", "")) if selected_history else "" }}" inputmode="numeric" autocomplete="off" placeholder="VD: 1.500.000">
                  </div>
                </div>
                <div class="field">
                  <label for="history-cost-type">Loại chi phí</label>
                  <select id="history-cost-type" name="cost_type">
                    <option value="repair" {{ 'selected' if normalize_history_cost_type(selected_history.get("LoaiChiPhi", "repair")) == "repair" else '' }}>Chi phí sửa chữa</option>
                    <option value="periodic" {{ 'selected' if normalize_history_cost_type(selected_history.get("LoaiChiPhi", "repair")) == "periodic" else '' }}>Chi phí bảo dưỡng định kỳ</option>
                  </select>
                </div>
                <div class="field">
                  <label for="history-content">Nội dung</label>
                  <textarea id="history-content" name="content" placeholder="Nhập nội dung bảo dưỡng" required>{{ selected_history.get("Nội dung", "") if selected_history else "" }}</textarea>
                </div>
                <button class="btn primary full" type="submit">{{ "Lưu thay đổi" if selected_history else "Thêm bản ghi" }}</button>
                {% if selected_history %}
                  <a class="btn full" href="{{ url_for('admin_data', tab='maintenance', plate=selected) }}">Hủy sửa</a>
                {% endif %}
              </form>
            </section>
          </div>

          <script>
            (() => {
              const moneyInputs = Array.from(document.querySelectorAll(".money-input"));
              if (!moneyInputs.length) return;

              const cleanMoney = (value) => value.replace(/[^\d]/g, "");
              const formatMoney = (value) => {
                const digits = cleanMoney(value);
                return digits ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".") : "";
              };

              moneyInputs.forEach((input) => {
                input.value = formatMoney(input.value);
                input.addEventListener("input", () => {
                  const formatted = formatMoney(input.value);
                  input.value = formatted;
                  input.setSelectionRange(formatted.length, formatted.length);
                });
              });
            })();
          </script>

          {% else %}
          <section class="panel">
            <div class="section-head">
              <div>
                <h2>Lịch sử của {{ selected }}</h2>
                <p class="subtle">Các bản ghi nhập từ web</p>
              </div>
              <span class="badge">{{ history_rows|length }} bản ghi</span>
            </div>
            {% if history_rows %}
              <div class="table-wrap admin-data-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Ngày</th>
                      <th>Loại</th>
                      <th>Nội dung</th>
                      <th>Chi phí</th>
                      <th>Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {% for row in history_rows %}
                      <tr>
                        <td data-label="Ngày">{{ row.date }}</td>
                        <td data-label="Loại"><span class="cost-type-pill {{ row.cost_type }}">{{ row.cost_type_label }}</span></td>
                        <td class="history-content" data-label="Nội dung">{{ row.content }}</td>
                        <td class="cost" data-label="Chi phí">{{ row.cost }}</td>
                        <td data-label="Thao tác">
                          <div class="history-actions">
                            <a class="btn primary" href="{{ url_for('admin_data', tab='maintenance', plate=selected, history_id=row.id) }}">Sửa</a>
                            <form method="post" onsubmit="return confirm('Xóa bản ghi này?')">
                              <input type="hidden" name="action" value="delete_history">
                              <input type="hidden" name="plate" value="{{ selected }}">
                              <input type="hidden" name="record_id" value="{{ row.id }}">
                              <button class="btn danger" type="submit">Xóa</button>
                            </form>
                          </div>
                        </td>
                      </tr>
                    {% endfor %}
                  </tbody>
                </table>
              </div>
            {% else %}
              <div class="empty">Xe này chưa có lịch sử bảo dưỡng.</div>
            {% endif %}
          </section>
        {% endif %}
        {% endif %}
          </div>
        </div>
        """,
        tab=tab,
        selected=selected,
        car=car,
        car_status=car_status,
        next_item=next_item,
        vehicle_rows=vehicle_rows,
        vehicle_page_rows=vehicle_page_rows,
        vehicle_page=vehicle_page,
        vehicle_total_pages=vehicle_total_pages,
        vehicle_total=vehicle_total,
        vehicle_all_total=vehicle_all_total,
        vehicle_start=vehicle_start,
        vehicle_end=vehicle_end,
        vehicle_mode=vehicle_mode,
        vehicle_query=vehicle_query,
        plates=plates,
        plate_labels=plate_labels,
        history_rows=history_rows,
        selected_history=selected_history,
        today=now_vn().strftime("%Y-%m-%d"),
        date_input_value=date_input_value,
        format_short_date=format_short_date,
        format_money_input=format_money_input,
        normalize_history_cost_type=normalize_history_cost_type,
    )
    return page(content, title="Quản lý dữ liệu xe", active="data")


@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin():
    if not is_admin():
        return Response("Forbidden", status=403)

    data = load_data(["xe", "access"])
    active_vehicles = active_vehicle_df(data["xe"])
    plates = safe_unique(active_vehicles, "Biển số")
    plate_labels = plate_label_map(active_vehicles)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            selected_plates = parse_plate_list(request.form.get("plates", ""))
            ttl_hours = parse_ttl_hours(request.form.get("ttl_hours", ACCESS_TTL_HOURS))
            invalid_plates = [plate for plate in selected_plates if plate not in plates]
            if not selected_plates:
                flash("Vui lòng chọn ít nhất một xe.", "error")
            elif invalid_plates:
                flash("Có biển số không hợp lệ.", "error")
            else:
                code, created_at = create_access_code(selected_plates, ttl_hours)
                flash(f"Đã tạo mã {code} cho {len(selected_plates)} xe, hiệu lực {ttl_hours} giờ.", "success")
        elif action == "revoke":
            code = request.form.get("code", "").strip()
            if code and revoke_access_code(code):
                flash(f"Đã thu hồi mã {code}.", "success")
            else:
                flash("Không tìm thấy mã cần thu hồi.", "error")
        elif action == "extend":
            code = request.form.get("code", "").strip()
            extra_hours = parse_ttl_hours(request.form.get("extra_hours", ACCESS_TTL_HOURS))
            if code and extend_access_code(code, extra_hours):
                flash(f"Đã gia hạn mã {code} thêm {extra_hours} giờ.", "success")
            else:
                flash("Không tìm thấy mã cần gia hạn.", "error")
        return redirect(url_for("admin"))

    access_rows = []
    if not data["access"].empty:
        for _, row in data["access"].iterrows():
            code = str(row.get("MaTruyCap", ""))
            if ADMIN_KEY and code == ADMIN_KEY:
                continue
            access_rows.append(
                {
                    "code": code,
                    "plate": format_plate_list(row.get("BienSo", ""), plate_labels),
                    "created": format_datetime_display(row.get("ThoiDiemCap", "")),
                    "ttl": f'{parse_ttl_hours(row.get("ThoiHanGio", ACCESS_TTL_HOURS))} giờ',
                    "remaining": remaining_text(row.get("ThoiDiemCap", ""), row.get("ThoiHanGio", ACCESS_TTL_HOURS)),
                    "active": access_is_active(row.get("ThoiDiemCap", ""), row.get("ThoiHanGio", ACCESS_TTL_HOURS)),
                    "sort_time": parse_cap_time_safe(row.get("ThoiDiemCap", "")),
                }
            )
    access_rows = sorted(access_rows, key=lambda item: item["sort_time"], reverse=True)

    audit_rows = []
    init_db()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT created_at, actor_code, action, plate, detail, ip_addr
            FROM audit_logs
            ORDER BY id DESC
            LIMIT 60
            """
        ).fetchall()
    for row in rows:
        actor = str(row["actor_code"] or "").strip() or "-"
        if ADMIN_KEY and actor == ADMIN_KEY:
            actor = "ADMIN"
        audit_rows.append(
            {
                "created_at": format_datetime_display(row["created_at"]),
                "actor": actor,
                "action": AUDIT_ACTION_LABELS.get(row["action"], row["action"] or ""),
                "plate": row["plate"] or "",
                "detail": row["detail"] or "",
                "ip_addr": row["ip_addr"] or "",
            }
        )

    admin_view = request.args.get("view", "access")
    if admin_view not in ("access", "audit"):
        admin_view = "access"

    content = render_template_string(
        r"""
        <section class="hero">
          <div>
            <h1>{{ 'Lịch sử truy cập' if admin_view == 'audit' else 'Quản lý mã truy cập' }}</h1>
          </div>
        </section>

        <div class="admin-shell">
          <aside class="panel admin-sidebar">
            <div>
              <h2>Quản trị</h2>
            </div>
            <nav class="admin-menu">
              <a href="{{ url_for('admin_data') }}">
                Quản lý xe
              </a>
              <a class="{{ 'active' if admin_view == 'access' else '' }}" href="{{ url_for('admin', view='access') }}">
                Quản lý mã truy cập
              </a>
              <a class="{{ 'active' if admin_view == 'audit' else '' }}" href="{{ url_for('admin', view='audit') }}">
                Lịch sử truy cập
              </a>
            </nav>
          </aside>
          <div class="admin-content">
          <div class="admin-layout no-form">
          {% if admin_view == 'access' %}
          <div class="inline-modal-backdrop" id="access-create-modal" hidden>
            <div class="inline-modal" role="dialog" aria-modal="true" aria-labelledby="access-create-modal-title">
              <div class="inline-modal-head">
                <h3 id="access-create-modal-title">Tạo mã mới</h3>
                <button class="btn" type="button" data-access-create-close aria-label="Đóng">&times;</button>
              </div>
              <div class="inline-modal-body">
                <form method="post" id="access-create-form">
                  <input type="hidden" name="action" value="create">
                  <input type="hidden" name="plates" id="admin-plate-value" value="">
                  <div class="field">
                    <label for="admin-plate-search">Xe được cấp quyền</label>
                    <div class="combo" id="admin-plate-combo">
                      <input
                        id="admin-plate-search"
                        value=""
                        placeholder="Tìm biển số hoặc loại xe"
                        autocomplete="off"
                      >
                      <button class="combo-toggle" type="button" aria-label="Mở danh sách xe">▼</button>
                      <div class="combo-menu" id="admin-plate-options">
                        {% for plate in plates %}
                          <button
                            class="combo-option"
                            type="button"
                            data-plate="{{ plate }}"
                            data-label="{{ plate_labels.get(plate, plate) }}"
                          >{{ plate_labels.get(plate, plate) }}</button>
                        {% endfor %}
                      </div>
                    </div>
                    <div class="selected-plates" id="admin-selected-plates"></div>
                  </div>
                  <div class="field">
                    <label for="ttl-hours">Thời hạn mã</label>
                    <div class="form-row">
                      <input id="ttl-hours" name="ttl_hours" type="number" min="1" max="720" value="24">
                      <span class="badge">giờ</span>
                    </div>
                  </div>
                  <div class="inline-modal-actions">
                    <button class="btn primary full" type="submit">Tạo mã truy cập</button>
                  </div>
                </form>
              </div>
            </div>
          </div>

          <script>
            (() => {
              const combo = document.getElementById("admin-plate-combo");
              const search = document.getElementById("admin-plate-search");
              const value = document.getElementById("admin-plate-value");
              const ttlHours = document.getElementById("ttl-hours");
              const modal = document.getElementById("access-create-modal");
              const closeButtons = document.querySelectorAll("[data-access-create-close]");
              const toggle = combo?.querySelector(".combo-toggle");
              const options = Array.from(document.querySelectorAll("#admin-plate-options .combo-option"));
              const selectedWrap = document.getElementById("admin-selected-plates");
              const form = document.getElementById("access-create-form");
              if (!combo || !search || !value || !toggle || !form || !selectedWrap || !modal) return;
              const selected = new Set(value.value ? value.value.split(",").filter(Boolean) : []);

              const openMenu = () => combo.classList.add("open");
              const closeMenu = () => combo.classList.remove("open");
              const renderSelected = () => {
                value.value = Array.from(selected).join(",");
                options.forEach((option) => option.classList.toggle("selected", selected.has(option.dataset.plate)));
                selectedWrap.innerHTML = "";
                if (!selected.size) {
                  selectedWrap.innerHTML = '<span class="subtle">Chưa chọn xe.</span>';
                  return;
                }
                selected.forEach((plate) => {
                  const option = options.find((item) => item.dataset.plate === plate);
                  const chip = document.createElement("span");
                  chip.className = "selected-chip";
                  const label = document.createElement("span");
                  label.textContent = option?.dataset.label || plate;
                  const remove = document.createElement("button");
                  remove.className = "chip-remove";
                  remove.type = "button";
                  remove.setAttribute("aria-label", "Xóa xe đã chọn");
                  remove.textContent = "×";
                  remove.addEventListener("click", () => {
                    selected.delete(plate);
                    renderSelected();
                  });
                  chip.appendChild(label);
                  chip.appendChild(remove);
                  selectedWrap.appendChild(chip);
                });
              };
              const filterOptions = (query = "") => {
                const normalized = query.trim().toLowerCase();
                options.forEach((option) => {
                  const label = option.dataset.label.toLowerCase();
                  const plate = option.dataset.plate.toLowerCase();
                  option.style.display = !normalized || label.includes(normalized) || plate.includes(normalized) ? "" : "none";
                });
              };
              const resetForm = () => {
                selected.clear();
                search.value = "";
                if (ttlHours) ttlHours.value = "24";
                filterOptions("");
                renderSelected();
                closeMenu();
              };
              const openModal = () => {
                modal.hidden = false;
                resetForm();
                search.focus();
              };
              const closeModal = () => {
                modal.hidden = true;
                closeMenu();
              };

              toggle.addEventListener("click", () => {
                filterOptions("");
                openMenu();
                search.focus();
              });
              search.addEventListener("focus", () => {
                search.select();
                filterOptions("");
                openMenu();
              });
              search.addEventListener("input", () => {
                filterOptions(search.value);
                openMenu();
              });
              options.forEach((option) => {
                option.addEventListener("click", () => {
                  if (selected.has(option.dataset.plate)) selected.delete(option.dataset.plate);
                  else selected.add(option.dataset.plate);
                  search.value = "";
                  filterOptions("");
                  renderSelected();
                });
              });
              form.addEventListener("submit", (event) => {
                if (!selected.size) event.preventDefault();
              });
              const copyText = async (text) => {
                if (navigator.clipboard?.writeText) {
                  await navigator.clipboard.writeText(text);
                  return;
                }
                const temp = document.createElement("textarea");
                temp.value = text;
                temp.setAttribute("readonly", "");
                temp.style.position = "fixed";
                temp.style.opacity = "0";
                document.body.appendChild(temp);
                temp.select();
                document.execCommand("copy");
                temp.remove();
              };
              const markCopied = (button) => {
                const hint = button.querySelector(".access-copy-hint");
                const oldText = hint?.textContent || "Copy";
                button.classList.add("copied");
                if (hint) hint.textContent = "Đã copy";
                window.setTimeout(() => {
                  button.classList.remove("copied");
                  if (hint) hint.textContent = oldText;
                }, 1400);
              };
              document.addEventListener("click", (event) => {
                const copyButton = event.target.closest("[data-copy-code]");
                if (copyButton) {
                  event.preventDefault();
                  copyText(copyButton.dataset.copyCode || "").then(() => markCopied(copyButton));
                  return;
                }
                const openTrigger = event.target.closest("[data-access-create-open]");
                if (openTrigger) {
                  event.preventDefault();
                  openModal();
                  return;
                }
                if (!combo.contains(event.target)) closeMenu();
              });
              closeButtons.forEach((button) => button.addEventListener("click", closeModal));
              modal.addEventListener("click", (event) => {
                if (event.target === modal) closeModal();
              });
              form.addEventListener("submit", () => {
                modal.hidden = true;
              });
              renderSelected();
            })();
          </script>

          <section class="panel admin-management-panel">
            <div class="section-head">
              <h2>Danh sách mã đang cấp</h2>
              <div class="toolbar">
                <button class="btn primary" type="button" data-access-create-open onclick="event.preventDefault(); const modal = document.getElementById('access-create-modal'); if (modal) { modal.hidden = false; const input = document.getElementById('admin-plate-search'); if (input) input.focus(); }">Thêm mới</button>
                <span class="badge">{{ access_rows|length }} mã</span>
              </div>
            </div>
            {% if access_rows %}
              <div class="access-code-list">
                {% for row in access_rows %}
                  <article class="access-code-card {{ 'active' if row.active else 'expired' }}">
                    <div class="access-code-main">
                      <span class="access-code-label">Mã truy cập</span>
                      <button class="access-copy-code" type="button" data-copy-code="{{ row.code }}" title="Bấm để copy mã">
                        <strong class="access-code-value code">{{ row.code }}</strong>
                        <span class="access-copy-hint">Copy</span>
                      </button>
                      <span class="status-pill {{ 'active' if row.active else 'expired' }}">
                        {{ 'Còn hạn' if row.active else 'Hết hạn' }}
                      </span>
                    </div>
                    <div class="access-code-meta">
                      <span class="access-code-label">Xe được cấp quyền</span>
                      <strong class="access-code-value access-code-plate">{{ row.plate }}</strong>
                    </div>
                    <div class="access-code-status">
                      <span class="access-code-label">Thời hạn</span>
                      <strong class="access-code-value">{{ row.ttl }}</strong>
                      <span class="subtle">Cấp lúc {{ row.created }}</span>
                      {% if row.active %}
                        <span class="access-remaining-text">Còn {{ row.remaining }}</span>
                      {% endif %}
                    </div>
                    <div class="access-card-actions">
                      <form class="extend-form access-extend-form" method="post">
                        <input type="hidden" name="action" value="extend">
                        <input type="hidden" name="code" value="{{ row.code }}">
                        <label class="extend-hours" title="Số giờ gia hạn">
                          <input name="extra_hours" type="number" min="1" max="720" value="24" aria-label="Số giờ gia hạn">
                          <span>giờ</span>
                        </label>
                        <button class="btn primary" type="submit">Gia hạn</button>
                      </form>
                      <form class="access-revoke-form" method="post">
                        <input type="hidden" name="action" value="revoke">
                        <input type="hidden" name="code" value="{{ row.code }}">
                        <button class="btn danger" type="submit">Thu hồi</button>
                      </form>
                    </div>
                  </article>
                {% endfor %}
              </div>
            {% else %}
              <div class="empty">Chưa có mã truy cập nào.</div>
            {% endif %}
          </section>

          {% else %}
          <section class="panel admin-management-panel">
            <div class="section-head">
              <h2>Lịch sử truy cập</h2>
              <span class="badge">{{ audit_rows|length }} mục gần nhất</span>
            </div>
            {% if audit_rows %}
              <div class="audit-log-list">
                {% for row in audit_rows %}
                  <article class="audit-log-card">
                    <div class="audit-log-time">
                      <span class="audit-log-label">Thời điểm</span>
                      <strong>{{ row.created_at }}</strong>
                      <span class="audit-log-ip">{{ row.ip_addr }}</span>
                    </div>
                    <div class="audit-log-body">
                      <span class="audit-log-label">Hành động</span>
                      <strong>{{ row.action }}</strong>
                      <div class="audit-log-detail">{{ row.detail }}</div>
                    </div>
                    <div class="audit-log-meta">
                      <span class="audit-log-label">Người thực hiện</span>
                      <strong>{{ row.actor }}</strong>
                      {% if row.plate and row.plate != '-' %}
                        <span class="badge">{{ row.plate }}</span>
                      {% endif %}
                    </div>
                  </article>
                {% endfor %}
              </div>
            {% else %}
              <div class="empty">Chưa có lịch sử truy cập.</div>
            {% endif %}
          </section>
          {% endif %}
        </div>
        </div>
        </div>
        """,
        plates=plates,
        plate_labels=plate_labels,
        access_rows=access_rows,
        audit_rows=audit_rows,
        admin_view=admin_view,
    )
    return page(content, title="Quản lý mã", active="admin")


@app.route("/export/<plate>")
@login_required
def export_excel(plate):
    data = load_data(["xe", "history"])
    if plate not in allowed_plates(data):
        return Response("Forbidden", status=403)

    df = data["history"]
    view = df[df["Biển số"].astype(str) == plate].copy()
    if not view.empty:
        view["Ngày"] = parse_date_series(view["Ngày"])
        view = view.dropna(subset=["Ngày"])
        view["Chi phí"] = pd.to_numeric(view["Chi phí"], errors="coerce").fillna(0)
        view = view.sort_values("Ngày", ascending=False)

    car_row = data["xe"][data["xe"]["Biển số"].astype(str) == plate]
    car_type = ""
    if not car_row.empty:
        car_type = str(car_row.iloc[0].get("Loại xe", "")).strip()

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book
        worksheet = workbook.add_worksheet("LichSuBaoDuong")

        title_fmt = workbook.add_format(
            {
                "bold": True,
                "font_size": 16,
                "font_color": "#FFFFFF",
                "bg_color": "#005BAA",
                "align": "center",
                "valign": "vcenter",
            }
        )
        subtitle_fmt = workbook.add_format(
            {
                "bold": True,
                "font_size": 12,
                "font_color": "#005BAA",
                "bg_color": "#EAF4FC",
                "align": "center",
                "valign": "vcenter",
            }
        )
        meta_label_fmt = workbook.add_format(
            {"bold": True, "font_color": "#5D7288", "align": "left", "valign": "vcenter"}
        )
        meta_value_fmt = workbook.add_format(
            {"bold": True, "font_color": "#09233F", "align": "left", "valign": "vcenter"}
        )
        header_fmt = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#005BAA",
                "border": 1,
                "border_color": "#C7D7E8",
                "align": "center",
                "valign": "vcenter",
            }
        )
        cell_fmt = workbook.add_format(
            {
                "border": 1,
                "border_color": "#D9E5F2",
                "valign": "top",
                "text_wrap": True,
            }
        )
        date_fmt = workbook.add_format(
            {
                "num_format": "dd/mm/yyyy",
                "border": 1,
                "border_color": "#D9E5F2",
                "align": "center",
                "valign": "top",
            }
        )
        money_fmt = workbook.add_format(
            {
                "num_format": '#,##0 "VND"',
                "border": 1,
                "border_color": "#D9E5F2",
                "align": "right",
                "valign": "top",
            }
        )
        empty_fmt = workbook.add_format(
            {
                "italic": True,
                "font_color": "#5D7288",
                "border": 1,
                "border_color": "#D9E5F2",
                "align": "center",
                "valign": "vcenter",
            }
        )

        worksheet.merge_range("A1:D1", "VIETINBANK CAR SERVICE PORTAL", title_fmt)
        worksheet.merge_range("A2:D2", "LỊCH SỬ BẢO DƯỠNG XE", subtitle_fmt)
        worksheet.write("A3", "Biển số", meta_label_fmt)
        worksheet.write("B3", plate, meta_value_fmt)
        worksheet.write("C3", "Loại xe", meta_label_fmt)
        worksheet.write("D3", car_type or "Chưa cập nhật", meta_value_fmt)
        worksheet.write("A4", "Ngày xuất", meta_label_fmt)
        worksheet.write("B4", now_vn().strftime("%d/%m/%Y %H:%M"), meta_value_fmt)

        headers = ["Biển số", "Ngày", "Nội dung bảo dưỡng", "Chi phí"]
        header_row = 5
        data_start = header_row + 1
        for col, header in enumerate(headers):
            worksheet.write(header_row, col, header, header_fmt)

        if view.empty:
            worksheet.merge_range(data_start, 0, data_start, 3, "Không có lịch sử bảo dưỡng.", empty_fmt)
            last_row = data_start
        else:
            for row_index, (_, row) in enumerate(view.iterrows(), start=data_start):
                worksheet.write(row_index, 0, row.get("Biển số", plate), cell_fmt)
                date_value = row.get("Ngày")
                if pd.notna(date_value):
                    worksheet.write_datetime(row_index, 1, date_value.to_pydatetime(), date_fmt)
                else:
                    worksheet.write(row_index, 1, "", cell_fmt)
                worksheet.write(row_index, 2, clean_history_content(row.get("Nội dung", "")), cell_fmt)
                worksheet.write_number(row_index, 3, float(row.get("Chi phí", 0)), money_fmt)
            last_row = data_start + len(view) - 1

        worksheet.autofilter(header_row, 0, max(last_row, header_row), 3)
        worksheet.freeze_panes(data_start, 0)
        worksheet.set_column("A:A", 16)
        worksheet.set_column("B:B", 14)
        worksheet.set_column("C:C", 72)
        worksheet.set_column("D:D", 18)
        worksheet.set_row(0, 28)
        worksheet.set_row(1, 24)
        worksheet.set_row(header_row, 24)
        for row_number in range(data_start, last_row + 1):
            worksheet.set_row(row_number, 72)
        worksheet.set_landscape()
        worksheet.fit_to_pages(1, 0)
        worksheet.set_margins(left=0.35, right=0.35, top=0.55, bottom=0.55)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"VietinBank_bao_duong_{plate}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8501")))
