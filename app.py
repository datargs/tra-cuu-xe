from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO
import hashlib
import hmac
import os
import random
import re
import secrets
import sqlite3
import string
import threading
import time
import unicodedata

from dotenv import load_dotenv
from flask import (
    abort,
    Flask,
    jsonify,
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
from werkzeug.exceptions import RequestEntityTooLarge


load_dotenv()
ADMIN_KEY = os.environ.get("TRACUUXE_ADMIN_KEY", "")
ACCESS_TTL_HOURS = 24
INACTIVITY_TIMEOUT_MINUTES = 15
CACHE_TTL_SECONDS = 900
DATA_DB_PATH = os.environ.get(
    "TRACUUXE_DB_PATH",
    os.path.join(os.environ.get("ProgramData", os.path.dirname(__file__)), "TraCuuXe", "tracuuxe.sqlite3"),
)
MAX_IMPORT_ROWS = 10000
MAX_IMPORT_COLUMNS = 32
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURE_WINDOW_SECONDS = 300
LOGIN_LOCKOUT_SECONDS = 300

MAIN_CENTER_CODE = "main"
AREA_ADMIN_SPECS = {
    "cua_nam": {
        "label": "Cửa Nam",
        "env": "TRACUUXE_CUANAM_ADMIN_KEY",
    },
    "bac_ha_noi": {
        "label": "Bắc Hà Nội",
        "env": "TRACUUXE_BHN_ADMIN_KEY",
    },
    "tay_ha_noi": {
        "label": "Tây Hà Nội",
        "env": "TRACUUXE_THN_ADMIN_KEY",
    },
    "vinh_phuc": {
        "label": "Vĩnh Phúc",
        "env": "TRACUUXE_VINHPHUC_ADMIN_KEY",
    },
    "hai_phong": {
        "label": "Hải Phòng",
        "env": "TRACUUXE_HAIPHONG_ADMIN_KEY",
    },
    "bac_ninh": {
        "label": "Bắc Ninh",
        "env": "TRACUUXE_BACNINH_ADMIN_KEY",
    },
}
AREA_ADMIN_KEYS = {
    area_code: os.environ.get(spec["env"], "").strip()
    for area_code, spec in AREA_ADMIN_SPECS.items()
}
CENTER_FILTER_ALL = "all"
CENTER_FILTER_LABELS = {
    CENTER_FILTER_ALL: "Tất cả",
    MAIN_CENTER_CODE: "Hội sở",
    **{area_code: spec["label"] for area_code, spec in AREA_ADMIN_SPECS.items()},
}
PARKING_LOCATION_OPTIONS = [
    "Hội sở",
    "Cửa Nam",
    "Bắc Hà Nội",
    "Tây Hà Nội",
    "Vĩnh Phúc",
    "Hải Phòng",
    "Bắc Ninh",
]


def default_parking_location(center):
    center_code = str(center or MAIN_CENTER_CODE).strip() or MAIN_CENTER_CODE
    return CENTER_FILTER_LABELS.get(center_code, CENTER_FILTER_LABELS[MAIN_CENTER_CODE])

SHEET_XE = "Xe"
SHEET_HISTORY = "Lịch sử bảo dưỡng"
SHEET_NEXT = "Lịch bảo dưỡng tiếp theo"
SHEET_ACCESS = "CapPhep"
SHEET_OIL = "Lịch sử thay dầu"
SHEET_REGISTRATION = "Lịch sử đăng kiểm"

XE_COLUMNS = ["Biển số", "Loại xe", "Năm sản xuất", "Trạng thái"]
CAR_CENTER_COLUMN = "TrungTam"
CAR_LOCATION_COLUMN = "DiaDiem"
XE_DATA_COLUMNS = XE_COLUMNS + [CAR_CENTER_COLUMN, CAR_LOCATION_COLUMN]
HISTORY_COLUMNS = ["ID", "Biển số", "Ngày", "Nội dung", "Chi phí", "LoaiChiPhi"]
NEXT_COLUMNS = ["Biển số", "Lịch bảo dưỡng gần nhất", "Dự kiến lần tiếp theo", "Gợi ý nội dung", "LoaiKeHoach", "Ngày đăng kiểm", "Hạn đăng kiểm đến", "Chi phí thay dầu", "Chi phí đăng kiểm"]
ACCESS_COLUMNS = ["MaTruyCap", "BienSo", "ThoiDiemCap", "ThoiHanGio"]
VEHICLE_STATUS_ACTIVE = "Đang hoạt động"
VEHICLE_STATUS_INACTIVE = "Ngừng hoạt động"
COST_TYPE_REPAIR = "repair"
COST_TYPE_PERIODIC = "periodic"
COST_TYPE_LABELS = {
    COST_TYPE_REPAIR: "Sửa chữa",
    COST_TYPE_PERIODIC: "Bảo dưỡng định kỳ",
}
MAINTENANCE_FOCUS_LABELS = {
    "periodic": "Bảo dưỡng định kỳ",
    "repair": "Sửa chữa",
    "registration": "Đăng kiểm",
    "oil": "Thay dầu",
}
PLAN_TYPE_REPAIR = COST_TYPE_REPAIR
PLAN_TYPE_PERIODIC = COST_TYPE_PERIODIC
PLAN_TYPE_LABELS = COST_TYPE_LABELS.copy()
PLAN_STATUS_PENDING = "pending"
PLAN_STATUS_COMPLETED = "completed"
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
    "save_maintenance_plan": "Lập/cập nhật kế hoạch dự kiến",
    "complete_maintenance_plan": "Cập nhật thực hiện kế hoạch",
    "save_registration": "Cập nhật đăng kiểm",
    "save_oil_record": "Lưu bản ghi thay dầu",
    "delete_oil_record": "Xóa bản ghi thay dầu",
    "save_registration_record": "Lưu bản ghi đăng kiểm",
    "delete_registration_record": "Xóa bản ghi đăng kiểm",
    "import_repair_records": "Nhập Excel sửa chữa",
    "import_oil_records": "Nhập Excel thay dầu",
    "add_history_record": "Thêm lịch sử bảo dưỡng",
    "update_history_record": "Sửa lịch sử bảo dưỡng",
    "delete_history_record": "Xóa lịch sử bảo dưỡng",
    "rotate_area_admin": "Đổi mã tài khoản khu vực",
    "toggle_area_admin": "Bật/tắt tài khoản khu vực",
}


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)

_cache = {"sheets": {}}
_login_failures = {}
_login_failures_lock = threading.Lock()

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


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(_error):
    return Response("File hoặc request vượt quá giới hạn 8 MB.", status=413)


@app.route("/healthz")
def healthz():
    try:
        init_db()
        with db_connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify({"status": "ok", "database": "ok"}), 200
    except Exception:
        app.logger.exception("Health check failed")
        return jsonify({"status": "error", "database": "unavailable"}), 503


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
    if not code:
        return None
    if ADMIN_KEY and hmac.compare_digest(code, ADMIN_KEY):
        return "root_admin"
    area_code = str(info.get("area") or info.get("center") or "").strip()
    if area_code in AREA_ADMIN_SPECS and area_admin_key_matches(area_code, code):
        return f"area_admin:{area_code}"
    fingerprint = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
    return f"access:{fingerprint}"


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


def hash_admin_key(value):
    value = str(value or "").strip()
    if not value:
        return ""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, 180000)
    return f"{salt.hex()}${digest.hex()}"


def verify_admin_key(stored_hash, value):
    try:
        salt_hex, digest_hex = str(stored_hash or "").split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", str(value or "").strip().encode("utf-8"), salt, 180000)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def key_hint(value):
    value = str(value or "").strip()
    if len(value) <= 4:
        return "••••"
    return f"{value[:2]}••••{value[-2:]}"


def db_connect():
    os.makedirs(os.path.dirname(DATA_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DATA_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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
                center TEXT NOT NULL DEFAULT 'main',
                parking_location TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS next_services (
                plate TEXT PRIMARY KEY,
                last_service TEXT,
                next_due TEXT,
                suggestion TEXT,
                plan_type TEXT DEFAULT 'periodic',
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
                plan_type TEXT DEFAULT 'periodic',
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
            CREATE TABLE IF NOT EXISTS oil_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL,
                oil_date TEXT NOT NULL,
                odometer INTEGER NOT NULL DEFAULT 0,
                previous_odometer INTEGER,
                cost REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS registration_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL,
                registration_date TEXT NOT NULL,
                due_month TEXT NOT NULL,
                cost REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS access_codes (
                code TEXT PRIMARY KEY,
                plates TEXT NOT NULL,
                created_at TEXT NOT NULL,
                ttl_hours INTEGER NOT NULL,
                issued_by TEXT,
                issued_area TEXT
            );
            CREATE TABLE IF NOT EXISTS area_admin_accounts (
                area_code TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                key_hash TEXT NOT NULL DEFAULT '',
                key_hint TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
        access_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(access_codes)").fetchall()
        }
        if "issued_by" not in access_columns:
            conn.execute("ALTER TABLE access_codes ADD COLUMN issued_by TEXT")
        if "issued_area" not in access_columns:
            conn.execute("ALTER TABLE access_codes ADD COLUMN issued_area TEXT")
        stamp = now_vn().strftime("%Y-%m-%d %H:%M")
        for area_code, spec in AREA_ADMIN_SPECS.items():
            env_key = AREA_ADMIN_KEYS.get(area_code, "")
            existing = conn.execute(
                "SELECT key_hash, active FROM area_admin_accounts WHERE area_code = ?",
                (area_code,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO area_admin_accounts
                        (area_code, label, key_hash, key_hint, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        area_code,
                        spec["label"],
                        hash_admin_key(env_key),
                        key_hint(env_key),
                        1 if env_key else 0,
                        stamp,
                        stamp,
                    ),
                )
            elif not existing["key_hash"] and env_key:
                conn.execute(
                    """
                    UPDATE area_admin_accounts
                    SET key_hash = ?, key_hint = ?, active = 1, updated_at = ?
                    WHERE area_code = ?
                    """,
                    (hash_admin_key(env_key), key_hint(env_key), stamp, area_code),
                )
        car_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(cars)").fetchall()
        }
        if "center" not in car_columns:
            conn.execute("ALTER TABLE cars ADD COLUMN center TEXT DEFAULT 'main'")
        if "parking_location" not in car_columns:
            conn.execute("ALTER TABLE cars ADD COLUMN parking_location TEXT")
        conn.execute(
            "UPDATE cars SET center = ? WHERE center IS NULL OR TRIM(center) = ''",
            (MAIN_CENTER_CODE,),
        )
        for center_code, location in {
            MAIN_CENTER_CODE: CENTER_FILTER_LABELS[MAIN_CENTER_CODE],
            **{area_code: spec["label"] for area_code, spec in AREA_ADMIN_SPECS.items()},
        }.items():
            conn.execute(
                """
                UPDATE cars
                SET parking_location = ?
                WHERE center = ? AND (parking_location IS NULL OR TRIM(parking_location) = '')
                """,
                (location, center_code),
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
        if "plan_type" not in next_columns:
            conn.execute("ALTER TABLE next_services ADD COLUMN plan_type TEXT DEFAULT 'periodic'")
        log_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(next_service_logs)").fetchall()
        }
        if "plan_type" not in log_columns:
            conn.execute("ALTER TABLE next_service_logs ADD COLUMN plan_type TEXT DEFAULT 'periodic'")
        migrate_legacy_auxiliary_history(conn)
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


def normalize_manufacture_year(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}", text):
        return ""
    year = int(text)
    return text if 1900 <= year <= now_vn().year + 1 else ""


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


def normalize_plan_type(value):
    return normalize_history_cost_type(value)


def normalize_maintenance_focus(value):
    value = str(value or "").strip().lower()
    return value if value in MAINTENANCE_FOCUS_LABELS else ""


def plan_type_label(value):
    return PLAN_TYPE_LABELS.get(normalize_plan_type(value), PLAN_TYPE_LABELS[PLAN_TYPE_PERIODIC])


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


def migrate_legacy_auxiliary_history(conn):
    """Move the old single-row oil/registration fields into their histories once."""
    marker = conn.execute(
        "SELECT value FROM app_meta WHERE key = 'auxiliary_history_migrated'"
    ).fetchone()
    if marker:
        return

    rows = conn.execute(
        """
        SELECT plate, registry_date, registry_due, registry_odo,
               oil_cost, registry_cost
        FROM next_services
        """
    ).fetchall()
    stamp = now_vn().strftime("%Y-%m-%d %H:%M")
    for row in rows:
        plate = normalize_plate(row["plate"])
        if not plate:
            continue
        registry_date = date_input_value(row["registry_date"])
        due_value = str(row["registry_due"] or "").strip()
        due_parsed = parse_user_date(due_value)
        due_month = due_parsed.strftime("%Y-%m") if due_parsed is not None else ""
        oil_cost = parse_money_value(row["oil_cost"])
        registry_cost = parse_money_value(row["registry_cost"])
        if registry_date and oil_cost > 0:
            conn.execute(
                """
                INSERT INTO oil_records
                    (plate, oil_date, odometer, previous_odometer, cost, created_at)
                SELECT ?, ?, 0, NULL, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM oil_records
                    WHERE plate = ? AND oil_date = ? AND cost = ?
                )
                """,
                (plate, registry_date, oil_cost, stamp, plate, registry_date, oil_cost),
            )
        if registry_date and due_month:
            conn.execute(
                """
                INSERT INTO registration_records
                    (plate, registration_date, due_month, cost, created_at)
                SELECT ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM registration_records
                    WHERE plate = ? AND registration_date = ? AND due_month = ? AND cost = ?
                )
                """,
                (
                    plate,
                    registry_date,
                    due_month,
                    registry_cost,
                    stamp,
                    plate,
                    registry_date,
                    due_month,
                    registry_cost,
                ),
            )
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES ('auxiliary_history_migrated', ?) \
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (stamp,),
    )


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
                    status AS 'Trạng thái',
                    COALESCE(center, 'main') AS 'TrungTam',
                    COALESCE(parking_location, '') AS 'DiaDiem'
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
                    COALESCE(plan_type, 'periodic') AS 'LoaiKeHoach',
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
                    ttl_hours AS 'ThoiHanGio',
                    issued_by AS 'NguoiCap',
                    issued_area AS 'DonViCap'
                FROM access_codes
                ORDER BY created_at DESC
                """,
                conn,
            )
        elif name == SHEET_OIL:
            df = pd.read_sql_query(
                """
                SELECT
                    id AS 'ID',
                    plate AS 'Biển số',
                    oil_date AS 'Ngày thay dầu',
                    odometer AS 'Số km khi thay dầu',
                    previous_odometer AS 'Số km trước',
                    cost AS 'Chi phí thay dầu'
                FROM oil_records
                ORDER BY oil_date DESC, id DESC
                """,
                conn,
            )
        elif name == SHEET_REGISTRATION:
            df = pd.read_sql_query(
                """
                SELECT
                    id AS 'ID',
                    plate AS 'Biển số',
                    registration_date AS 'Ngày đăng kiểm',
                    due_month AS 'Tháng đăng kiểm tiếp theo',
                    cost AS 'Chi phí đăng kiểm'
                FROM registration_records
                ORDER BY registration_date DESC, id DESC
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
        "xe": (SHEET_XE, XE_DATA_COLUMNS),
        "history": (SHEET_HISTORY, None),
        "next": (SHEET_NEXT, None),
        "access": (SHEET_ACCESS, ["MaTruyCap", "BienSo", "ThoiDiemCap", "ThoiHanGio", "NguoiCap", "DonViCap"]),
        "oil": (SHEET_OIL, None),
        "registration": (SHEET_REGISTRATION, None),
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


def gen_area_admin_key():
    return secrets.token_hex(8).upper()


def area_admin_accounts():
    init_db()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT area_code, label, key_hint, active, created_at, updated_at
            FROM area_admin_accounts
            ORDER BY area_code
            """
        ).fetchall()
    return [dict(row) for row in rows]


def area_admin_account(area_code):
    area_code = str(area_code or "").strip()
    if area_code not in AREA_ADMIN_SPECS:
        return None
    init_db()
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM area_admin_accounts WHERE area_code = ?",
            (area_code,),
        ).fetchone()
    return row


def area_admin_key_matches(area_code, raw_key):
    row = area_admin_account(area_code)
    if row is not None:
        return bool(row["active"] and verify_admin_key(row["key_hash"], raw_key))
    fallback = AREA_ADMIN_KEYS.get(area_code, "")
    return bool(fallback and hmac.compare_digest(fallback, str(raw_key or "").strip()))


def set_area_admin_key(area_code, custom_key=""):
    area_code = str(area_code or "").strip()
    spec = AREA_ADMIN_SPECS.get(area_code)
    if not spec:
        return None, "Không tìm thấy tài khoản khu vực."
    new_key = str(custom_key or "").strip().upper()
    if not new_key:
        new_key = gen_area_admin_key()
    elif not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{5,63}", new_key):
        return None, "Mã tự đặt phải dài 6-64 ký tự, chỉ gồm chữ, số, dấu chấm, gạch ngang hoặc gạch dưới."
    stamp = now_vn().strftime("%Y-%m-%d %H:%M")
    init_db()
    with db_connect() as conn:
        if ADMIN_KEY and hmac.compare_digest(new_key, ADMIN_KEY.upper()):
            return None, "Mã này đang được dùng cho admin tổng."
        account_rows = conn.execute(
            "SELECT area_code, key_hash FROM area_admin_accounts WHERE area_code <> ?",
            (area_code,),
        ).fetchall()
        if any(verify_admin_key(row["key_hash"], new_key) for row in account_rows):
            return None, "Mã này đang được dùng cho một tài khoản khu vực khác."
        if conn.execute("SELECT 1 FROM access_codes WHERE code = ?", (new_key,)).fetchone():
            return None, "Mã này đang được dùng cho một mã truy cập xe."
        conn.execute(
            """
            UPDATE area_admin_accounts
            SET key_hash = ?, key_hint = ?, active = 1, updated_at = ?
            WHERE area_code = ?
            """,
            (hash_admin_key(new_key), key_hint(new_key), stamp, area_code),
        )
    audit_log("rotate_area_admin", f"Đổi mã quản trị khu vực {spec['label']}")
    return new_key, ""


def rotate_area_admin_key(area_code):
    new_key, _error = set_area_admin_key(area_code)
    return new_key


def set_area_admin_active(area_code, active):
    area_code = str(area_code or "").strip()
    if area_code not in AREA_ADMIN_SPECS:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute(
            "UPDATE area_admin_accounts SET active = ?, updated_at = ? WHERE area_code = ?",
            (1 if active else 0, now_vn().strftime("%Y-%m-%d %H:%M"), area_code),
        )
    if result.rowcount:
        audit_log(
            "toggle_area_admin",
            f"{'Bật' if active else 'Tắt'} tài khoản quản trị {AREA_ADMIN_SPECS[area_code]['label']}",
        )
    return result.rowcount > 0


def login_client_key():
    return (request.remote_addr or "unknown").strip()[:120]


def login_is_rate_limited():
    now = time.monotonic()
    with _login_failures_lock:
        record = _login_failures.get(login_client_key())
        if not record:
            return False, 0
        if now - record["last"] > LOGIN_FAILURE_WINDOW_SECONDS:
            _login_failures.pop(login_client_key(), None)
            return False, 0
        if record.get("blocked_until", 0) > now:
            return True, int(record["blocked_until"] - now) + 1
        return False, 0


def register_login_failure():
    now = time.monotonic()
    key = login_client_key()
    with _login_failures_lock:
        record = _login_failures.get(key)
        if not record or now - record["last"] > LOGIN_FAILURE_WINDOW_SECONDS:
            record = {"count": 0, "last": now, "blocked_until": 0}
        record["count"] += 1
        record["last"] = now
        if record["count"] >= LOGIN_MAX_FAILURES:
            record["blocked_until"] = now + LOGIN_LOCKOUT_SECONDS
        _login_failures[key] = record


def clear_login_failures():
    with _login_failures_lock:
        _login_failures.pop(login_client_key(), None)


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
    return bool(ADMIN_KEY and info and hmac.compare_digest(str(info.get("code", "")), ADMIN_KEY))


def admin_actor_codes():
    codes = []
    if ADMIN_KEY:
        codes.append("root_admin")
    codes.extend(f"area_admin:{area_code}" for area_code in AREA_ADMIN_SPECS)
    return list(dict.fromkeys(codes))


def admin_actor_label(code):
    code = str(code or "").strip()
    if code == "root_admin" or (ADMIN_KEY and hmac.compare_digest(code, ADMIN_KEY)):
        return "Quản trị cao nhất"
    if code.startswith("area_admin:"):
        area_code = code.split(":", 1)[1]
        return f"Quản trị khu vực: {CENTER_FILTER_LABELS.get(area_code, area_code)}"
    for area_code, area_key in AREA_ADMIN_KEYS.items():
        if area_key and hmac.compare_digest(code, area_key):
            return f"Quản trị khu vực: {CENTER_FILTER_LABELS.get(area_code, area_code)}"
    return "Quản trị hệ thống"


def current_area_code():
    info = current_access()
    if not info or info.get("role") != "area_admin":
        return None
    area_code = str(info.get("area") or info.get("center") or "").strip()
    if area_code not in AREA_ADMIN_SPECS or not area_admin_key_matches(area_code, info.get("code")):
        return None
    return area_code


def is_area_admin():
    return bool(current_area_code())


def is_center_admin():
    """Backward-compatible internal name for an area administrator."""
    return is_area_admin()


def current_area_label():
    return CENTER_FILTER_LABELS.get(current_area_code() or "", "")


def can_manage_data():
    return is_admin() or is_area_admin()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        info = current_access()
        if not info:
            return redirect(url_for("login"))

        if (ADMIN_KEY and hmac.compare_digest(str(info.get("code", "")), ADMIN_KEY)) or is_area_admin():
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


def parse_integer_value(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if pd.isna(value):
                return 0
        except Exception:
            pass
        return int(value)
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else 0


def normalize_month_input(value):
    parsed = parse_user_date(value)
    return parsed.strftime("%Y-%m") if parsed is not None else ""


def format_month_display(value):
    month = normalize_month_input(value)
    if not month:
        return "Chưa cập nhật"
    return f"{month[5:7]}/{month[:4]}"


def add_one_year(value):
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, day=28)


def maintenance_schedule(history_df, plate):
    """Calculate the next periodic maintenance from the latest actual record."""
    empty = {
        "latest_date": "",
        "latest_display": "Chưa cập nhật",
        "next_date": "",
        "next_month": "Chưa cập nhật",
        "next_tooltip": "",
        "state": due_state(""),
    }
    if history_df is None or history_df.empty or "Biển số" not in history_df.columns:
        return empty
    view = history_df[history_df["Biển số"].astype(str) == normalize_plate(plate)].copy()
    if view.empty:
        return empty
    if "LoaiChiPhi" in view.columns:
        view = view[view["LoaiChiPhi"].apply(normalize_history_cost_type) == COST_TYPE_PERIODIC]
    if view.empty or "Ngày" not in view.columns:
        return empty
    view["_date"] = parse_date_series(view["Ngày"])
    view = view.dropna(subset=["_date"])
    if view.empty:
        return empty
    latest = view["_date"].max().to_pydatetime()
    next_date = add_one_year(latest)
    next_iso = next_date.strftime("%Y-%m-%d")
    state = due_state(next_iso)
    state["date"] = next_date.strftime("%m/%Y")
    state["tooltip"] = next_date.strftime("%d/%m/%Y")
    return {
        "latest_date": latest.strftime("%Y-%m-%d"),
        "latest_display": latest.strftime("%d/%m/%Y"),
        "next_date": next_iso,
        "next_month": next_date.strftime("%m/%Y"),
        "next_tooltip": next_date.strftime("%d/%m/%Y"),
        "state": state,
    }


def build_oil_rows(df, plate):
    if df is None or df.empty:
        return []
    view = df[df["Biển số"].astype(str) == normalize_plate(plate)].copy()
    rows = []
    for _, row in view.iterrows():
        current_km = parse_integer_value(row.get("Số km khi thay dầu"))
        previous_km = parse_integer_value(row.get("Số km trước"))
        rows.append(
            {
                "id": str(row.get("ID", "")),
                "date": format_short_date(row.get("Ngày thay dầu")),
                "date_raw": date_input_value(row.get("Ngày thay dầu")),
                "odometer": f"{current_km:,}".replace(",", ".") if current_km else "-",
                "odometer_raw": str(current_km or ""),
                "previous_odometer": f"{previous_km:,}".replace(",", ".") if previous_km else "-",
                "previous_odometer_raw": str(previous_km or ""),
                "distance": f"{current_km - previous_km:,}".replace(",", ".") if current_km and previous_km else "-",
                "cost": format_vnd(row.get("Chi phí thay dầu")),
                "cost_raw": format_money_input(row.get("Chi phí thay dầu")),
            }
        )
    return rows


def build_registration_rows(df, plate):
    if df is None or df.empty:
        return []
    view = df[df["Biển số"].astype(str) == normalize_plate(plate)].copy()
    rows = []
    for _, row in view.iterrows():
        rows.append(
            {
                "id": str(row.get("ID", "")),
                "date": format_short_date(row.get("Ngày đăng kiểm")),
                "date_raw": date_input_value(row.get("Ngày đăng kiểm")),
                "due_month": format_month_display(row.get("Tháng đăng kiểm tiếp theo")),
                "due_month_raw": normalize_month_input(row.get("Tháng đăng kiểm tiếp theo")),
                "cost": format_vnd(row.get("Chi phí đăng kiểm")),
                "cost_raw": format_money_input(row.get("Chi phí đăng kiểm")),
            }
        )
    return rows


def save_oil_record(plate, oil_date, odometer, previous_odometer, cost, record_id=""):
    plate = normalize_plate(plate)
    date_value = date_input_value(oil_date)
    current_km = parse_integer_value(odometer)
    if not plate or not date_value or current_km <= 0:
        return False, "Vui lòng nhập ngày thay dầu và số kilomet hiện tại."
    init_db()
    with db_connect() as conn:
        if record_id:
            current = conn.execute(
                "SELECT odometer FROM oil_records WHERE id = ? AND plate = ?",
                (record_id, plate),
            ).fetchone()
        else:
            current = None
        previous_km = parse_integer_value(previous_odometer)
        if previous_km <= 0:
            query = "SELECT odometer FROM oil_records WHERE plate = ?"
            args = [plate]
            if record_id:
                query += " AND id <> ?"
                args.append(record_id)
            query += " ORDER BY oil_date DESC, id DESC LIMIT 1"
            latest = conn.execute(query, args).fetchone()
            previous_km = int(latest["odometer"]) if latest and latest["odometer"] else 0
        if not current and previous_km <= 0:
            return False, "Lần nhập thay dầu đầu tiên cần nhập số kilomet trước."
        if previous_km and current_km < previous_km:
            return False, "Số kilomet khi thay dầu phải lớn hơn số kilomet trước."
        amount = parse_money_value(cost)
        stamp = now_vn().strftime("%Y-%m-%d %H:%M")
        if record_id:
            result = conn.execute(
                """
                UPDATE oil_records
                SET oil_date = ?, odometer = ?, previous_odometer = ?, cost = ?
                WHERE id = ? AND plate = ?
                """,
                (date_value, current_km, previous_km or None, amount, record_id, plate),
            )
        else:
            result = conn.execute(
                """
                INSERT INTO oil_records
                    (plate, oil_date, odometer, previous_odometer, cost, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (plate, date_value, current_km, previous_km or None, amount, stamp),
            )
        if result.rowcount != 1:
            return False, "Không tìm thấy bản ghi thay dầu cần cập nhật."
        conn.commit()
    clear_cache()
    audit_log("save_oil_record", f"Lưu bản ghi thay dầu cho xe {plate}", plate)
    return True, "Đã lưu bản ghi thay dầu."


def delete_oil_record(record_id, plate):
    init_db()
    with db_connect() as conn:
        result = conn.execute("DELETE FROM oil_records WHERE id = ? AND plate = ?", (record_id, normalize_plate(plate)))
        conn.commit()
    if result.rowcount:
        clear_cache()
        audit_log("delete_oil_record", f"Xóa bản ghi thay dầu #{record_id}", plate)
        return True
    return False


def save_registration_record(plate, registration_date, due_month, cost, record_id=""):
    plate = normalize_plate(plate)
    date_value = date_input_value(registration_date)
    month_value = normalize_month_input(due_month)
    if not plate or not date_value or not month_value:
        return False, "Vui lòng nhập ngày đăng kiểm và tháng dự kiến đăng kiểm tiếp theo."
    amount = parse_money_value(cost)
    init_db()
    with db_connect() as conn:
        if record_id:
            result = conn.execute(
                """
                UPDATE registration_records
                SET registration_date = ?, due_month = ?, cost = ?
                WHERE id = ? AND plate = ?
                """,
                (date_value, month_value, amount, record_id, plate),
            )
        else:
            result = conn.execute(
                """
                INSERT INTO registration_records
                    (plate, registration_date, due_month, cost, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (plate, date_value, month_value, amount, now_vn().strftime("%Y-%m-%d %H:%M")),
            )
        if result.rowcount != 1:
            return False, "Không tìm thấy bản ghi đăng kiểm cần cập nhật."
        conn.commit()
    clear_cache()
    audit_log("save_registration_record", f"Lưu bản ghi đăng kiểm cho xe {plate}", plate)
    return True, "Đã lưu bản ghi đăng kiểm."


def delete_registration_record(record_id, plate):
    init_db()
    with db_connect() as conn:
        result = conn.execute(
            "DELETE FROM registration_records WHERE id = ? AND plate = ?",
            (record_id, normalize_plate(plate)),
        )
        conn.commit()
    if result.rowcount:
        clear_cache()
        audit_log("delete_registration_record", f"Xóa bản ghi đăng kiểm #{record_id}", plate)
        return True
    return False


def normalize_import_header(value):
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def import_column(frame, aliases):
    wanted = {normalize_import_header(alias) for alias in aliases}
    for column in frame.columns:
        if normalize_import_header(column) in wanted:
            return column
    return None


def import_date_value(value):
    parsed = parse_user_date(value)
    return parsed.strftime("%Y-%m-%d") if parsed is not None else ""


def import_repair_records(upload, scope_plates):
    if upload is None or not upload.filename:
        return False, "Vui lòng chọn file Excel sửa chữa."
    try:
        frame = pd.read_excel(upload, dtype=object)
    except Exception:
        return False, "Không đọc được file Excel. Hãy kiểm tra đúng mẫu và định dạng file."
    if len(frame.index) > MAX_IMPORT_ROWS or len(frame.columns) > MAX_IMPORT_COLUMNS:
        return False, f"File vượt giới hạn {MAX_IMPORT_ROWS} dòng hoặc {MAX_IMPORT_COLUMNS} cột."
    plate_col = import_column(frame, ["Biển số", "Biển số xe", "Plate"])
    date_col = import_column(frame, ["Ngày sửa chữa", "Ngày", "Repair date"])
    content_col = import_column(frame, ["Nội dung sửa chữa", "Nội dung", "Repair content"])
    cost_col = import_column(frame, ["Số tiền", "Chi phí", "Chi phí sửa chữa", "Cost"])
    if not all((plate_col, date_col, content_col, cost_col)):
        return False, "File sửa chữa phải có các cột: Biển số, Ngày sửa chữa, Nội dung sửa chữa, Số tiền."
    rows = []
    scope = set(scope_plates)
    for index, item in frame.iterrows():
        if all(pd.isna(item.get(column)) or not str(item.get(column)).strip() for column in (plate_col, date_col, content_col, cost_col)):
            continue
        plate = normalize_plate(item.get(plate_col))
        date_value = import_date_value(item.get(date_col))
        content = str(item.get(content_col) or "").strip()
        if plate not in scope:
            return False, f"Dòng {index + 2}: biển số {plate or '(trống)'} không thuộc phạm vi quản lý."
        if not date_value or not content:
            return False, f"Dòng {index + 2}: cần có ngày sửa chữa và nội dung sửa chữa."
        rows.append((plate, date_value, content, parse_money_value(item.get(cost_col))))
    if not rows:
        return False, "File không có dòng sửa chữa hợp lệ."
    init_db()
    with db_connect() as conn:
        conn.executemany(
            "INSERT INTO service_history (plate, service_date, content, cost, cost_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(plate, date_value, content, cost, COST_TYPE_REPAIR, now_vn().strftime("%Y-%m-%d %H:%M")) for plate, date_value, content, cost in rows],
        )
        conn.commit()
    clear_cache(SHEET_HISTORY)
    import_counts = {}
    for row in rows:
        import_counts[row[0]] = import_counts.get(row[0], 0) + 1
    for plate, count in sorted(import_counts.items()):
        audit_log("import_repair_records", f"Nhập {count} bản ghi sửa chữa từ Excel", plate)
    return True, f"Đã nhập {len(rows)} bản ghi sửa chữa."


def import_oil_records(upload, scope_plates):
    if upload is None or not upload.filename:
        return False, "Vui lòng chọn file Excel thay dầu."
    try:
        frame = pd.read_excel(upload, dtype=object)
    except Exception:
        return False, "Không đọc được file Excel. Hãy kiểm tra đúng mẫu và định dạng file."
    if len(frame.index) > MAX_IMPORT_ROWS or len(frame.columns) > MAX_IMPORT_COLUMNS:
        return False, f"File vượt giới hạn {MAX_IMPORT_ROWS} dòng hoặc {MAX_IMPORT_COLUMNS} cột."
    plate_col = import_column(frame, ["Biển số", "Biển số xe", "Plate"])
    date_col = import_column(frame, ["Ngày thay dầu", "Ngày", "Oil date"])
    current_col = import_column(frame, ["Số km khi thay dầu", "Số kilomet khi thay dầu", "Kilomet hiện tại", "Odometer"])
    previous_col = import_column(frame, ["Số km trước", "Số kilomet trước", "Odometer trước"])
    cost_col = import_column(frame, ["Chi phí thay dầu", "Chi phí", "Số tiền", "Cost"])
    if not all((plate_col, date_col, current_col, cost_col)):
        return False, "File thay dầu phải có các cột: Biển số, Ngày thay dầu, Số km khi thay dầu, Chi phí thay dầu."
    scope = set(scope_plates)
    rows = []
    previous_by_plate = {}
    init_db()
    with db_connect() as conn:
        for plate in scope:
            latest = conn.execute("SELECT odometer FROM oil_records WHERE plate = ? ORDER BY oil_date DESC, id DESC LIMIT 1", (plate,)).fetchone()
            previous_by_plate[plate] = int(latest["odometer"]) if latest and latest["odometer"] else 0
        for index, item in frame.iterrows():
            values = (item.get(plate_col), item.get(date_col), item.get(current_col), item.get(cost_col))
            if all(pd.isna(value) or not str(value).strip() for value in values):
                continue
            plate = normalize_plate(item.get(plate_col))
            date_value = import_date_value(item.get(date_col))
            current_km = parse_integer_value(item.get(current_col))
            previous_km = parse_integer_value(item.get(previous_col)) if previous_col else 0
            if plate not in scope:
                return False, f"Dòng {index + 2}: biển số {plate or '(trống)'} không thuộc phạm vi quản lý."
            if not date_value or current_km <= 0:
                return False, f"Dòng {index + 2}: cần có ngày thay dầu và số kilomet khi thay dầu."
            if previous_km <= 0:
                previous_km = previous_by_plate.get(plate, 0)
            if previous_km <= 0:
                return False, f"Dòng {index + 2}: lần thay dầu đầu tiên của {plate} phải có Số km trước."
            if current_km < previous_km:
                return False, f"Dòng {index + 2}: số km khi thay dầu phải lớn hơn hoặc bằng số km trước."
            rows.append((plate, date_value, current_km, previous_km, parse_money_value(item.get(cost_col))))
            previous_by_plate[plate] = current_km
        if not rows:
            return False, "File không có dòng thay dầu hợp lệ."
        conn.executemany(
            "INSERT INTO oil_records (plate, oil_date, odometer, previous_odometer, cost, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(plate, date_value, current_km, previous_km, cost, now_vn().strftime("%Y-%m-%d %H:%M")) for plate, date_value, current_km, previous_km, cost in rows],
        )
        conn.commit()
    clear_cache(SHEET_OIL)
    import_counts = {}
    for row in rows:
        import_counts[row[0]] = import_counts.get(row[0], 0) + 1
    for plate, count in sorted(import_counts.items()):
        audit_log("import_oil_records", f"Nhập {count} bản ghi thay dầu từ Excel", plate)
    return True, f"Đã nhập {len(rows)} bản ghi thay dầu."


def latest_maintenance_date(history_df, plate, fallback=""):
    """Return the latest actual maintenance/repair date for a vehicle."""
    if history_df is not None and not history_df.empty and "Biển số" in history_df.columns:
        view = history_df[history_df["Biển số"].astype(str) == normalize_plate(plate)].copy()
        if not view.empty and "Ngày" in view.columns:
            dates = parse_date_series(view["Ngày"]).dropna()
            if not dates.empty:
                return dates.max().strftime("%Y-%m-%d")
    return date_input_value(fallback)


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


def pending_maintenance_plan_alerts():
    """Automatic reminders derived from the latest actual histories."""
    info = current_access()
    if not info:
        return []
    allowed = None
    center = None
    if is_area_admin():
        center = current_area_code()
    elif is_admin():
        center = None
    else:
        allowed = parse_plate_list(info.get("bien_so"))

    data = load_data(["xe", "history", "registration"])
    vehicles = data["xe"]
    if center:
        vehicles = center_filtered_vehicle_df(vehicles, center)
    if allowed is not None and allowed != ["ALL"]:
        vehicles = vehicles[vehicles["Biển số"].astype(str).isin(set(allowed))]
    plates = safe_unique(active_vehicle_df(vehicles), "Biển số")
    today = now_vn().date()
    alerts = []
    car_map = {}
    for _, row in vehicles.iterrows():
        plate = normalize_plate(row.get("Biển số"))
        car_map[plate] = row.to_dict()
    for plate in plates:
        schedule = maintenance_schedule(data["history"], plate)
        due = parse_due_date(schedule.get("next_date"))
        if due and due.date() <= today:
            overdue_days = max(0, (today - due.date()).days)
            alerts.append({
                "plate": plate,
                "car_type": car_map.get(plate, {}).get("Loại xe", ""),
                "planned_date": due.strftime("%d/%m/%Y"),
                "planned_date_raw": due.strftime("%Y-%m-%d"),
                "content": "Bảo dưỡng định kỳ",
                "plan_type": COST_TYPE_PERIODIC,
                "plan_type_label": "Bảo dưỡng định kỳ",
                "center": str(car_map.get(plate, {}).get(CAR_CENTER_COLUMN, MAIN_CENTER_CODE) or MAIN_CENTER_CODE),
                "center_label": CENTER_FILTER_LABELS.get(str(car_map.get(plate, {}).get(CAR_CENTER_COLUMN, MAIN_CENTER_CODE) or MAIN_CENTER_CODE), "Không xác định"),
                "overdue_days": overdue_days,
                "kind": "maintenance",
            })
    # Compatibility for old databases: preserve an existing due plan until
    # the operator records its first actual maintenance entry.
    legacy = load_data(["next"])["next"]
    if not legacy.empty:
        for _, row in legacy[legacy["Biển số"].astype(str).isin(plates)].iterrows():
            plate = normalize_plate(row.get("Biển số"))
            if any(item["plate"] == plate and item["kind"] == "maintenance" for item in alerts):
                continue
            due = parse_due_date(row.get("Dự kiến lần tiếp theo"))
            if due is None or due.date() > today:
                continue
            overdue_days = max(0, (today - due.date()).days)
            alerts.append({
                "plate": plate,
                "car_type": car_map.get(plate, {}).get("Loại xe", ""),
                "planned_date": due.strftime("%d/%m/%Y"),
                "planned_date_raw": due.strftime("%Y-%m-%d"),
                "content": row.get("Gợi ý nội dung", "") or "Bảo dưỡng định kỳ",
                "plan_type": normalize_plan_type(row.get("LoaiKeHoach")),
                "plan_type_label": plan_type_label(row.get("LoaiKeHoach")),
                "center": str(car_map.get(plate, {}).get(CAR_CENTER_COLUMN, MAIN_CENTER_CODE) or MAIN_CENTER_CODE),
                "center_label": CENTER_FILTER_LABELS.get(str(car_map.get(plate, {}).get(CAR_CENTER_COLUMN, MAIN_CENTER_CODE) or MAIN_CENTER_CODE), "Không xác định"),
                "overdue_days": overdue_days,
                "kind": "maintenance",
            })
    registration = data.get("registration", pd.DataFrame())
    if not registration.empty:
        registration = registration[registration["Biển số"].astype(str).isin(plates)].copy()
        registration["_date"] = parse_date_series(registration["Ngày đăng kiểm"])
        for plate, group in registration.groupby("Biển số"):
            group = group.sort_values(["_date", "ID"], ascending=False)
            row = group.iloc[0]
            due_month = normalize_month_input(row.get("Tháng đăng kiểm tiếp theo"))
            due = parse_user_date(f"{due_month}-01") if due_month else None
            if due is None or due.date() > today:
                continue
            overdue_days = max(0, (today - due.date()).days)
            alerts.append({
                "plate": str(plate),
                "car_type": car_map.get(str(plate), {}).get("Loại xe", ""),
                "planned_date": format_month_display(due_month),
                "planned_date_raw": due.strftime("%Y-%m-%d"),
                "content": "Đăng kiểm",
                "plan_type": "registry",
                "plan_type_label": "Đăng kiểm",
                "center": str(car_map.get(str(plate), {}).get(CAR_CENTER_COLUMN, MAIN_CENTER_CODE) or MAIN_CENTER_CODE),
                "center_label": CENTER_FILTER_LABELS.get(str(car_map.get(str(plate), {}).get(CAR_CENTER_COLUMN, MAIN_CENTER_CODE) or MAIN_CENTER_CODE), "Không xác định"),
                "overdue_days": overdue_days,
                "kind": "registration",
            })
    alerts.sort(key=lambda item: (item["overdue_days"] == 0, -item["overdue_days"], item["plate"]))
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
    df_oil = data.get("oil", pd.DataFrame())
    df_registration = data.get("registration", pd.DataFrame())
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

        oil_view = df_oil[df_oil["Biển số"].astype(str) == plate].copy() if not df_oil.empty else pd.DataFrame()
        registration_view = (
            df_registration[df_registration["Biển số"].astype(str) == plate].copy()
            if not df_registration.empty
            else pd.DataFrame()
        )
        if not oil_view.empty:
            oil_cost = pd.to_numeric(oil_view["Chi phí thay dầu"], errors="coerce").fillna(0).sum()
            history_count += len(oil_view)
        else:
            oil_cost = parse_money_value(next_item.get("Chi phí thay dầu"))
        if not registration_view.empty:
            registry_cost = pd.to_numeric(registration_view["Chi phí đăng kiểm"], errors="coerce").fillna(0).sum()
            history_count += len(registration_view)
        else:
            registry_cost = parse_money_value(next_item.get("Chi phí đăng kiểm"))
        total_cost = repair_cost + periodic_cost + oil_cost + registry_cost

        schedule = maintenance_schedule(df_history, plate)
        maintenance = schedule["state"] if schedule["next_date"] else due_state("")
        if not registration_view.empty:
            registration_view["_date"] = parse_date_series(registration_view["Ngày đăng kiểm"])
            latest_registration = registration_view.sort_values(["_date", "ID"], ascending=False).iloc[0]
            registry = due_state(f"{normalize_month_input(latest_registration.get('Tháng đăng kiểm tiếp theo'))}-01")
        else:
            registry = due_state("")
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
    df_oil = data.get("oil", pd.DataFrame())
    df_registration = data.get("registration", pd.DataFrame())
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

    oil_plates = set(df_oil[plate_col].astype(str)) if not df_oil.empty and plate_col in df_oil.columns else set()
    registration_plates = set(df_registration[plate_col].astype(str)) if not df_registration.empty and plate_col in df_registration.columns else set()
    if not df_oil.empty and plate_col in df_oil.columns:
        oil_view = df_oil[df_oil[plate_col].astype(str).isin(plates)].copy()
        for _, row in oil_view.iterrows():
            plate = str(row.get(plate_col, ""))
            oil_date = parse_user_date(row.get("Ngày thay dầu"))
            oil_amount = parse_money_value(row.get("Chi phí thay dầu"))
            if oil_date is None or oil_amount <= 0:
                continue
            all_dates.append(oil_date)
            counts = plate_type_counts.setdefault(plate, {"repair": 0, "periodic": 0, "oil": 0, "registry": 0})
            counts["oil"] = counts.get("oil", 0) + 1
            events.append({
                "plate": plate,
                "date": oil_date,
                "amount": oil_amount,
                "category": "oil",
                "detail": "Chi phí thay dầu",
                "source": "Lịch sử thay dầu",
            })

    if not df_registration.empty and plate_col in df_registration.columns:
        registration_view = df_registration[df_registration[plate_col].astype(str).isin(plates)].copy()
        for _, row in registration_view.iterrows():
            plate = str(row.get(plate_col, ""))
            registration_date = parse_user_date(row.get("Ngày đăng kiểm"))
            registration_amount = parse_money_value(row.get("Chi phí đăng kiểm"))
            if registration_date is None or registration_amount <= 0:
                continue
            all_dates.append(registration_date)
            counts = plate_type_counts.setdefault(plate, {"repair": 0, "periodic": 0, "oil": 0, "registry": 0})
            counts["registry"] = counts.get("registry", 0) + 1
            events.append({
                "plate": plate,
                "date": registration_date,
                "amount": registration_amount,
                "category": "registry",
                "detail": "Chi phí đăng kiểm",
                "source": "Lịch sử đăng kiểm",
            })

    if not df_next.empty and plate_col in df_next.columns:
        next_view = df_next[df_next[plate_col].astype(str).isin(plates)].copy()
        if not next_view.empty:
            for _, row in next_view.iterrows():
                plate = str(row.get(plate_col, ""))
                registration_date = parse_user_date(row.get("Ngày đăng kiểm"))
                if plate in oil_plates and plate in registration_plates:
                    continue
                oil_date = registration_date
                oil_amount = parse_money_value(row.get("Chi phí thay dầu"))
                if plate not in oil_plates and oil_date is not None and oil_amount > 0:
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
                if plate not in registration_plates and registry_date is not None and registry_amount > 0:
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
                count_parts.append({"key": "periodic", "text": f'{counts["periodic"]} bảo dưỡng'})
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


def pagination_items(current, total):
    """Return compact page numbers with ellipses for long paginated lists."""
    total = max(1, int(total or 1))
    current = min(max(1, int(current or 1)), total)
    if total <= 7:
        return list(range(1, total + 1))

    visible = {1, total}
    visible.update(range(max(1, current - 1), min(total, current + 1) + 1))
    result = []
    for page_number in sorted(visible):
        if result and page_number - result[-1] > 1:
            result.append("ellipsis")
        result.append(page_number)
    return result


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


def access_code_plates(code):
    """Return the vehicle plates assigned to an access code."""
    code = str(code or "").strip()
    if not code:
        return []
    init_db()
    with db_connect() as conn:
        row = conn.execute(
            "SELECT plates FROM access_codes WHERE code = ?",
            (code,),
        ).fetchone()
    return parse_plate_list(row["plates"]) if row else []


def access_code_is_in_scope(code, scope_plates):
    """Area admins may only manage codes assigned entirely to their vehicles."""
    if not is_area_admin():
        return True
    plates = access_code_plates(code)
    return bool(plates) and set(plates).issubset(set(scope_plates))


def format_plate_list(value, labels=None):
    labels = labels or {}
    plates = parse_plate_list(value)
    if plates == ["ALL"]:
        return "Tất cả xe"
    return "\n".join(labels.get(plate, plate) for plate in plates)


def scoped_vehicle_df(df):
    """Return only vehicles visible to the current management scope."""
    if df is None or df.empty or not is_area_admin():
        return df.copy() if df is not None else pd.DataFrame(columns=XE_DATA_COLUMNS)
    if CAR_CENTER_COLUMN not in df.columns:
        return df.iloc[0:0].copy()
    area_code = current_area_code()
    return df[
        df[CAR_CENTER_COLUMN].fillna(MAIN_CENTER_CODE).astype(str).str.strip()
        == area_code
    ].copy()


def current_center_scope():
    return current_area_code()


def normalize_center_filter(value=None):
    """Resolve the module selected by an administrator."""
    if is_area_admin():
        return current_area_code()
    candidate = str(value or "").strip()
    if candidate not in CENTER_FILTER_LABELS:
        candidate = str(session.get("admin_center_filter", CENTER_FILTER_ALL)).strip()
    if candidate not in CENTER_FILTER_LABELS:
        candidate = CENTER_FILTER_ALL
    return candidate


def center_filtered_vehicle_df(df, center_filter):
    if df is None:
        return pd.DataFrame(columns=XE_DATA_COLUMNS)
    if center_filter == CENTER_FILTER_ALL:
        return df.copy()
    if CAR_CENTER_COLUMN not in df.columns:
        return df.iloc[0:0].copy()
    return df[
        df[CAR_CENTER_COLUMN].fillna(MAIN_CENTER_CODE).astype(str).str.strip()
        == center_filter
    ].copy()


def scoped_vehicle_plates(data, include_inactive=True):
    vehicles = scoped_vehicle_df(data["xe"])
    if not include_inactive:
        vehicles = active_vehicle_df(vehicles)
    return set(safe_unique(vehicles, "Biển số"))


def allowed_plates(data):
    info = current_access()
    selected_plates = parse_plate_list(info.get("bien_so"))
    active_vehicles = scoped_vehicle_df(active_vehicle_df(data["xe"]))
    active_plates = safe_unique(active_vehicles, "Biển số")
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
                COALESCE(plan_type, 'periodic') AS plan_type,
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
                "plan_type": normalize_plan_type(row["plan_type"]),
                "plan_type_label": plan_type_label(row["plan_type"]),
                "registry_date": row["registry_date"] or "Chưa cập nhật",
                "registry_due": row["registry_due"] or "Chưa cập nhật",
                "oil_cost": format_vnd(row["oil_cost"]),
                "registry_cost": format_vnd(row["registry_cost"]),
                "saved_at": format_datetime_display(row["saved_at"]),
                "last_service_raw": date_input_value(row["last_service"]),
                "next_due_raw": date_input_value(row["next_due"]),
                "suggestion_raw": row["suggestion"] or "",
                "plan_type_raw": normalize_plan_type(row["plan_type"]),
                "registry_date_raw": date_input_value(row["registry_date"]),
                "registry_due_raw": date_input_value(row["registry_due"]),
                "oil_cost_raw": format_money_input(row["oil_cost"]),
                "registry_cost_raw": format_money_input(row["registry_cost"]),
            }
        )
    return items


def create_access_code(plates, ttl_hours=ACCESS_TTL_HOURS, issued_area=None):
    code = gen_access_code()
    created_at = now_vn().strftime("%Y-%m-%d %H:%M")
    ttl_hours = parse_ttl_hours(ttl_hours)
    issued_by = current_actor_code() or "system"
    issued_area = issued_area or current_area_code() or MAIN_CENTER_CODE
    init_db()
    with db_connect() as conn:
        while conn.execute("SELECT 1 FROM access_codes WHERE code = ?", (code,)).fetchone():
            code = gen_access_code()
        conn.execute(
            """
            INSERT INTO access_codes (code, plates, created_at, ttl_hours, issued_by, issued_area)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (code, ",".join(plates), created_at, ttl_hours, issued_by, issued_area),
        )
    clear_cache(SHEET_ACCESS)
    audit_log("create_access_code", f"Tạo mã truy cập cho {len(plates)} xe")
    return code, created_at


def revoke_access_code(code):
    init_db()
    with db_connect() as conn:
        result = conn.execute("DELETE FROM access_codes WHERE code = ?", (code,))
    clear_cache(SHEET_ACCESS)
    if result.rowcount > 0:
        audit_log("revoke_access_code", "Thu hồi một mã truy cập")
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
    audit_log("extend_access_code", f"Gia hạn một mã truy cập thêm {extra_hours} giờ")
    return True


def upsert_vehicle(plate, car_type, manufacture_year, status, parking_location="", center=None):
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        existing = conn.execute(
            "SELECT center, parking_location FROM cars WHERE plate = ?",
            (plate,),
        ).fetchone()
        if center is None:
            center = (existing["center"] if existing else None) or MAIN_CENTER_CODE
        parking_location = str(parking_location or "").strip()
        if not parking_location and existing:
            parking_location = str(existing["parking_location"] or "").strip()
        if not parking_location:
            parking_location = default_parking_location(center)
        conn.execute(
            """
            INSERT INTO cars (plate, car_type, manufacture_year, status, center, parking_location, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plate) DO UPDATE SET
                car_type = excluded.car_type,
                manufacture_year = excluded.manufacture_year,
                status = excluded.status,
                center = excluded.center,
                parking_location = excluded.parking_location,
                updated_at = excluded.updated_at
            """,
            (
                plate,
                str(car_type or "").strip(),
                normalize_manufacture_year(manufacture_year),
                normalize_vehicle_status(status),
                str(center or MAIN_CENTER_CODE).strip(),
                parking_location,
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


def _existing_next_service(conn, plate):
    return conn.execute(
        "SELECT * FROM next_services WHERE plate = ?",
        (normalize_plate(plate),),
    ).fetchone()


def save_maintenance_plan(plate, planned_date, content, plan_type=PLAN_TYPE_PERIODIC):
    """Save a future maintenance/repair plan without any actual cost."""
    plate = normalize_plate(plate)
    planned = parse_user_date(planned_date)
    content = str(content or "").strip()
    plan_type = normalize_plan_type(plan_type)
    if not plate:
        return False, "Vui lòng chọn xe trước khi thêm kế hoạch."
    if planned is None:
        return False, "Vui lòng nhập ngày kế hoạch hợp lệ."
    if planned.date() <= now_vn().date():
        return False, "Ngày kế hoạch phải lớn hơn ngày hiện tại."
    if not content:
        return False, "Vui lòng nhập nội dung dự kiến."

    init_db()
    planned_iso = planned.strftime("%Y-%m-%d")
    saved_at = now_vn().strftime("%Y-%m-%d %H:%M")
    with db_connect() as conn:
        current = _existing_next_service(conn, plate)
        if current and parse_due_date(current["next_due"]):
            if parse_due_date(current["next_due"]).date() <= now_vn().date():
                return False, "Kế hoạch đã đến hạn. Hãy cập nhật thực hiện trước khi lập kế hoạch mới."
        last_service = str(current["last_service"] or "") if current else ""
        registry_due = str(current["registry_due"] or "") if current else ""
        registry_odo = str(current["registry_odo"] or "") if current else ""
        registry_date = str(current["registry_date"] or "") if current else ""
        oil_cost = parse_money_value(current["oil_cost"] if current else 0)
        registry_cost = parse_money_value(current["registry_cost"] if current else 0)
        conn.execute(
            """
            INSERT INTO next_service_logs
            (plate, last_service, next_due, suggestion, plan_type, registry_date, registry_due, oil_cost, registry_cost, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (plate, last_service, planned_iso, content, plan_type, registry_date, registry_due, oil_cost, registry_cost, saved_at),
        )
        conn.execute(
            """
            INSERT INTO next_services
            (plate, last_service, next_due, suggestion, plan_type, registry_due, registry_odo, registry_date, oil_cost, registry_cost, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plate) DO UPDATE SET
                last_service = excluded.last_service,
                next_due = excluded.next_due,
                suggestion = excluded.suggestion,
                plan_type = excluded.plan_type,
                updated_at = excluded.updated_at
            """,
            (plate, last_service, planned_iso, content, plan_type, registry_due, registry_odo, registry_date, oil_cost, registry_cost, saved_at),
        )
    clear_cache(SHEET_NEXT)
    audit_log("save_maintenance_plan", f"Lập kế hoạch {planned_iso} cho xe {plate}", plate)
    return True, "Đã lưu kế hoạch dự kiến."


def complete_maintenance_plan(plate, actual_date, content, cost, cost_type=PLAN_TYPE_PERIODIC):
    """Convert an due plan into one actual history record."""
    plate = normalize_plate(plate)
    content = str(content or "").strip()
    actual = parse_user_date(actual_date)
    amount = parse_money_value(cost)
    cost_type = normalize_plan_type(cost_type)
    if not plate:
        return False, "Vui lòng chọn xe trước khi cập nhật thực hiện."
    if not content:
        return False, "Vui lòng nhập nội dung thực tế."
    if actual is None:
        return False, "Vui lòng nhập ngày thực hiện hợp lệ."
    if actual.date() > now_vn().date():
        return False, "Ngày thực hiện không được lớn hơn ngày hiện tại."
    if amount <= 0:
        return False, "Vui lòng nhập số tiền thực tế lớn hơn 0."

    init_db()
    actual_iso = actual.strftime("%Y-%m-%d")
    with db_connect() as conn:
        current = _existing_next_service(conn, plate)
        if not current or not parse_due_date(current["next_due"]):
            return False, "Xe này chưa có kế hoạch dự kiến để cập nhật thực hiện."
        planned = parse_due_date(current["next_due"])
        if planned.date() > now_vn().date():
            return False, "Chưa đến ngày kế hoạch, chưa thể nhập phần thực hiện."
        if actual.date() < planned.date():
            return False, "Ngày thực hiện phải từ ngày kế hoạch trở đi."
        conn.execute(
            """
            INSERT INTO service_history (plate, service_date, content, cost, cost_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (plate, actual_iso, content, amount, cost_type, now_vn().strftime("%Y-%m-%d %H:%M")),
        )
        conn.execute(
            """
            UPDATE next_services
            SET last_service = ?, next_due = NULL, suggestion = NULL, oil_cost = 0, updated_at = ?
            WHERE plate = ?
            """,
            (actual_iso, now_vn().strftime("%Y-%m-%d %H:%M"), plate),
        )
    clear_cache()
    audit_log("complete_maintenance_plan", f"Cập nhật thực hiện kế hoạch cho xe {plate}: {amount:g}", plate)
    return True, "Đã chuyển kế hoạch thành bản ghi lịch sử thực tế."


def save_registration(plate, registry_date, registry_due, registry_cost=0, registry_odo=""):
    """Update inspection fields while leaving the maintenance plan untouched."""
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        current = _existing_next_service(conn, plate)
        values = {
            "last_service": current["last_service"] if current else "",
            "next_due": current["next_due"] if current else None,
            "suggestion": current["suggestion"] if current else "",
            "plan_type": current["plan_type"] if current else PLAN_TYPE_PERIODIC,
            "registry_due": str(registry_due or "").strip(),
            "registry_odo": str(registry_odo or "").strip(),
            "registry_date": str(registry_date or "").strip(),
            "oil_cost": parse_money_value(current["oil_cost"] if current else 0),
            "registry_cost": parse_money_value(registry_cost),
            "updated_at": now_vn().strftime("%Y-%m-%d %H:%M"),
        }
        conn.execute(
            """
            INSERT INTO next_services
            (plate, last_service, next_due, suggestion, plan_type, registry_due, registry_odo, registry_date, oil_cost, registry_cost, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plate) DO UPDATE SET
                registry_due = excluded.registry_due,
                registry_odo = excluded.registry_odo,
                registry_date = excluded.registry_date,
                registry_cost = excluded.registry_cost,
                updated_at = excluded.updated_at
            """,
            (plate, values["last_service"], values["next_due"], values["suggestion"], values["plan_type"], values["registry_due"], values["registry_odo"], values["registry_date"], values["oil_cost"], values["registry_cost"], values["updated_at"]),
        )
    clear_cache(SHEET_NEXT)
    audit_log("save_registration", f"Cập nhật đăng kiểm cho xe {plate}", plate)
    return True


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
            SELECT last_service, next_due, suggestion, plan_type, registry_date, registry_due, oil_cost, registry_cost, saved_at
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
                (plate, last_service, next_due, suggestion, plan_type, registry_due, registry_date, oil_cost, registry_cost, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plate) DO UPDATE SET
                    last_service = excluded.last_service,
                    next_due = excluded.next_due,
                    suggestion = excluded.suggestion,
                    plan_type = excluded.plan_type,
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
                    row["plan_type"],
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


def update_next_service_log(record_id, plate, last_service, next_due, suggestion, registry_date, registry_due, oil_cost, registry_cost, plan_type=PLAN_TYPE_PERIODIC):
    plate = normalize_plate(plate)
    if not record_id or not plate:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute(
            """
            UPDATE next_service_logs
            SET last_service = ?, next_due = ?, suggestion = ?, plan_type = ?, registry_date = ?, registry_due = ?, oil_cost = ?, registry_cost = ?
            WHERE id = ? AND plate = ?
            """,
            (
                str(last_service or "").strip(),
                str(next_due or "").strip(),
                str(suggestion or "").strip(),
                normalize_plan_type(plan_type),
                str(registry_date or "").strip(),
                str(registry_due or "").strip(),
                parse_money_value(oil_cost),
                parse_money_value(registry_cost),
                record_id,
                plate,
            ),
        )
    if result.rowcount > 0:
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
        audit_log("delete_next_log", f"Xóa dòng lịch kế hoạch #{record_id} của xe {plate}", plate)
    return result.rowcount > 0


def add_history_record(plate, service_date, content, cost, cost_type=COST_TYPE_REPAIR):
    plate = normalize_plate(plate)
    date_value = date_input_value(service_date)
    content_value = str(content or "").strip()
    if not plate or not date_value or not content_value:
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
                date_value,
                content_value,
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
    date_value = date_input_value(service_date)
    content_value = str(content or "").strip()
    if not record_id or not plate or not date_value or not content_value:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute(
            """
            UPDATE service_history
            SET plate = ?, service_date = ?, content = ?, cost = ?, cost_type = ?
            WHERE id = ? AND plate = ?
            """,
            (
                plate,
                date_value,
                content_value,
                parse_money_value(cost),
                normalize_history_cost_type(cost_type),
                record_id,
                plate,
            ),
        )
    clear_cache(SHEET_HISTORY)
    if result.rowcount > 0:
        audit_log("update_history_record", f"Sửa lịch sử bảo dưỡng #{record_id} của xe {plate}", plate)
    return result.rowcount > 0


def delete_history_record(record_id, plate=""):
    init_db()
    with db_connect() as conn:
        if plate:
            result = conn.execute("DELETE FROM service_history WHERE id = ? AND plate = ?", (record_id, normalize_plate(plate)))
        else:
            result = conn.execute("DELETE FROM service_history WHERE id = ?", (record_id,))
    clear_cache(SHEET_HISTORY)
    if result.rowcount > 0:
        audit_log("delete_history_record", f"Xóa lịch sử bảo dưỡng #{record_id}")
    return result.rowcount > 0


def delete_maintenance_plan(plate):
    plate = normalize_plate(plate)
    if not plate:
        return False
    init_db()
    with db_connect() as conn:
        result = conn.execute(
            "UPDATE next_services SET next_due = NULL, suggestion = NULL, oil_cost = 0, updated_at = ? WHERE plate = ?",
            (now_vn().strftime("%Y-%m-%d %H:%M"), plate),
        )
    clear_cache(SHEET_NEXT)
    if result.rowcount > 0:
        audit_log("delete_next_service", f"Xóa kế hoạch bảo dưỡng của xe {plate}", plate)
    return result.rowcount > 0


def delete_next_service(plate):
    """Backward-compatible alias; only removes the maintenance plan now."""
    return delete_maintenance_plan(plate)


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
      --bg: #f3f7fb;
      --panel: #ffffff;
      --ink: #17324d;
      --muted: #6c7f92;
      --line: #d8e3ee;
      --soft: #f7fafd;
      --brand: #005baa;
      --brand-dark: #00457f;
      --brand-soft: #edf5fb;
      --accent: #c84a59;
      --accent-dark: #a83948;
      --danger: #b85e6a;
      --ok: #5b9278;
      --shadow: 0 12px 28px rgba(0, 43, 88, .07);
    }
    * { box-sizing: border-box; }
    *, *::before, *::after {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      font-family: "SVN-Gilroy", "Roboto", Arial, Helvetica, sans-serif;
      background:
        linear-gradient(180deg, #fbfdff 0, #f3f7fb 280px, #eef4fa 100%);
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
      border-bottom: 3px solid var(--accent);
      position: sticky;
      top: 0;
      z-index: 10;
      box-shadow: 0 8px 20px rgba(0, 54, 108, .12);
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
    .plan-reminder {
      border-color: #f2d27a !important;
      background: #fff7df !important;
      color: #946200 !important;
      gap: 6px;
      min-height: 38px !important;
      padding: 10px 13px !important;
      font-size: 14px !important;
      line-height: 1 !important;
    }
    .plan-reminder-count {
      min-width: 18px;
      height: 18px;
      padding: 0 4px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #c62828;
      color: #fff;
      font-size: 11px;
      line-height: 1;
    }
    .btn.primary:hover { background: linear-gradient(180deg, #005baa, #00457f); color: #fff; }
    .page {
      width: min(1760px, calc(100% - 36px));
      margin: 20px auto 38px;
    }
    .dashboard-page .page {
      margin-top: 6px;
    }
    .dashboard-page .dashboard-hero {
      display: none;
    }
    .dashboard-module-switcher {
      display: flex;
      justify-content: flex-end;
      margin-bottom: 8px;
    }
    .dashboard-module-switcher .toolbar {
      flex-wrap: wrap;
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
    .center-filter-toolbar {
      flex: 0 0 auto !important;
      width: auto !important;
      flex-wrap: nowrap;
      justify-content: flex-end;
      gap: 8px;
    }
    .center-filter-toolbar .btn {
      white-space: nowrap;
    }
    .center-filter-toolbar .btn.active {
      border-color: var(--brand) !important;
      background: linear-gradient(180deg, #0068bd, #005baa) !important;
      color: #fff !important;
      font-weight: 800 !important;
      box-shadow: 0 4px 10px rgba(0, 91, 170, .18);
    }
    .center-filter-toolbar .btn.active:hover {
      background: linear-gradient(180deg, #005baa, #00457f) !important;
      color: #fff !important;
    }
    h1 { margin: 0; font-size: 28px; letter-spacing: 0; font-weight: 800; }
    h2 { margin: 0 0 10px; font-size: 18px; font-weight: 800; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    .subtle { color: var(--muted); font-size: 14px; }
    .hero > div > p.subtle,
    .section-head > div > p.subtle,
    .public-data-zone-head > p {
      display: none;
    }
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
    label.required-field { color: #b3133b; }
    .required-mark { color: #e0194d; font-weight: 900; }
    .required-input { border-color: #e0194d; box-shadow: 0 0 0 1px rgba(224, 25, 77, .12); }
    .required-hint { color: #b3133b; font-size: 12px; font-weight: 700; }
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
      grid-template-columns: 1fr;
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
      grid-template-columns: repeat(5, minmax(0, 1fr));
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
    .workflow-guide {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .workflow-action {
      display: flex;
      align-items: center;
      gap: 9px;
      min-height: 58px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fbfdff;
    }
    .workflow-action.ready {
      border-color: #9cc9ed;
      background: #f2f8fe;
    }
    .workflow-action.attention {
      border-color: #f2d27a;
      background: #fff9e9;
    }
    .workflow-action.muted { opacity: .82; }
    .workflow-number {
      width: 27px;
      height: 27px;
      flex: 0 0 27px;
      display: grid;
      place-items: center;
      border-radius: 50%;
      background: var(--brand);
      color: #fff;
      font-size: 13px;
      font-weight: 800;
    }
    .workflow-action.attention .workflow-number { background: #d88900; }
    .workflow-copy { min-width: 0; flex: 1; }
    .workflow-copy strong { display: block; font-size: 13px; line-height: 1.2; }
    .workflow-copy span { display: block; margin-top: 3px; color: var(--muted); font-size: 11px; line-height: 1.25; }
    .workflow-action .btn { min-height: 32px; padding: 7px 9px; font-size: 12px; white-space: nowrap; }
    .workflow-cards {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr);
      gap: 10px;
    }
    .workflow-card {
      display: grid;
      gap: 10px;
      padding: 13px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
    }
    .workflow-card.plan-card { border-top: 3px solid #d88900; }
    .workflow-card.registry-card { border-top: 3px solid var(--brand); }
    .workflow-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
    .workflow-card-head h3 { margin: 0; font-size: 16px; }
    .workflow-card-head p { margin: 3px 0 0; color: var(--muted); font-size: 12px; line-height: 1.3; }
    .workflow-status { white-space: nowrap; }
    .workflow-data { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .workflow-data-item { padding: 9px; border: 1px solid var(--line); border-radius: 8px; background: #fbfdff; }
    .workflow-data-item.full { grid-column: 1 / -1; }
    .workflow-data-item span { display: block; margin-bottom: 4px; color: var(--muted); font-size: 11px; font-weight: 700; }
    .workflow-data-item strong { display: block; font-size: 14px; line-height: 1.3; overflow-wrap: anywhere; }
    .workflow-empty { padding: 11px; border: 1px dashed #b8c9da; border-radius: 8px; color: var(--muted); background: #fbfdff; font-size: 13px; line-height: 1.4; }
    .workflow-inline-step { display: grid; gap: 8px; padding-top: 2px; }
    .workflow-inline-step h3 { margin: 0; font-size: 15px; }
    .workflow-note { color: var(--muted); font-size: 12px; line-height: 1.4; }
    .workflow-card-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .workflow-card-actions .btn { min-height: 36px; }
    .workflow-history { border-top: 1px solid var(--line); padding-top: 10px; }
    .workflow-history summary { cursor: pointer; color: var(--brand-dark); font-size: 13px; font-weight: 800; }
    .workflow-history[open] summary { margin-bottom: 10px; }
    .legacy-plan-content { display: none; }
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
    .reminder-list { display: grid; gap: 10px; }
    .reminder-row {
      display: grid;
      grid-template-columns: minmax(180px, .85fr) minmax(170px, .7fr) minmax(280px, 1.5fr) auto;
      gap: 14px;
      align-items: center;
      padding: 13px 14px;
      border: 1px solid var(--line);
      border-left: 4px solid #d88900;
      border-radius: 10px;
      background: #fffdf7;
    }
    .reminder-row.overdue { border-left-color: #e0194d; background: #fffafa; }
    .reminder-cell span { display: block; margin-bottom: 4px; color: var(--muted); font-size: 11px; font-weight: 700; }
    .reminder-cell strong { display: block; line-height: 1.35; overflow-wrap: anywhere; }
    .reminder-cell small { display: block; margin-top: 4px; color: #946200; font-size: 12px; font-weight: 700; }
    .reminder-row.overdue .reminder-cell small { color: #b3133b; }
    .reminder-vehicle strong { font-size: 16px; color: var(--brand-dark); }
    .reminder-action { justify-self: end; white-space: nowrap; }
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
    .audit-filter {
      display: grid;
      gap: 12px;
      margin: 0 0 18px;
      padding: 14px;
      border: 1px solid #cfe0ef;
      border-radius: 10px;
      background: #f8fbfe;
    }
    .audit-filter-grid {
      display: grid;
      grid-template-columns: minmax(220px, 1.5fr) repeat(4, minmax(140px, 1fr));
      gap: 10px;
    }
    .audit-filter .form-actions { margin: 0; }
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
      font-weight: 500;
      letter-spacing: .018em;
      font-variant-numeric: tabular-nums;
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
      font-weight: 500;
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
    .dashboard-vehicle-table th,
    .dashboard-vehicle-table td {
      padding: 8px 12px;
    }
    .dashboard-vehicle-table th {
      font-size: 12px;
    }
    .dashboard-vehicle-table td {
      font-size: 14px;
    }
    .dashboard-vehicle-table .due-pill {
      padding: 4px 7px;
      font-size: 12px;
    }
    .dashboard-vehicle-table td > .subtle {
      line-height: 1.2;
    }
    .dashboard-vehicle-table .cost-cell-breakdown {
      gap: 1px;
      margin-top: 3px;
      font-size: 11px;
      line-height: 1.2;
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
    .dashboard-status-legend {
      width: 100%;
      border: 0;
      border-radius: 6px;
      padding: 4px 6px;
      background: transparent;
      font: inherit;
      color: inherit;
      text-align: left;
      cursor: pointer;
    }
    .dashboard-status-legend:hover {
      background: #f3f8fd;
    }
    .dashboard-status-legend:focus-visible {
      outline: 2px solid var(--brand);
      outline-offset: 1px;
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
    .pagination-ellipsis {
      min-width: 20px;
      padding: 0 2px;
      color: var(--muted);
      font-weight: 800;
      text-align: center;
      user-select: none;
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
      .workflow-guide, .workflow-cards { grid-template-columns: 1fr; }
      .workflow-action { align-items: flex-start; }
      .schedule-log-row { grid-template-columns: 1fr; }
      .reminder-row { grid-template-columns: 1fr; gap: 9px; }
      .reminder-action { justify-self: start; }
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
      .vehicle-compact-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
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
      .center-filter-toolbar {
        width: 100% !important;
        flex-wrap: wrap;
      }
      .center-filter-toolbar > .btn {
        flex: 0 1 auto;
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
      .audit-filter-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
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
      .audit-filter-grid {
        grid-template-columns: 1fr;
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
    /* Soft VietinBank palette: keep the brand blue, soften secondary states. */
    .btn.primary {
      background: linear-gradient(180deg, #0b6bb3, #005baa);
    }
    .btn.primary:hover {
      background: linear-gradient(180deg, #005baa, #004f91);
    }
    .login-page {
      background:
        linear-gradient(120deg, rgba(255,255,255,.12) 0 18%, transparent 18% 100%),
        linear-gradient(135deg, #174f7f 0%, #005baa 52%, #5f536f 100%);
    }
    .login-visual {
      background:
        linear-gradient(160deg, rgba(0,69,127,.98), rgba(0,91,170,.96) 62%, rgba(200,74,89,.78)),
        #00457f;
    }
    label.required-field,
    .required-mark,
    .required-hint {
      color: #a85f6b;
    }
    .required-input {
      border-color: #b85e6a;
      box-shadow: 0 0 0 1px rgba(184, 94, 106, .12);
    }
    .plan-step {
      border-left-color: #5c8fb5;
      background: #fbfdff;
    }
    .plan-step.periodic {
      border-left-color: #5b9278;
    }
    .plan-step.registry,
    .plan-cost.registry {
      border-left-color: #5c8fb5;
    }
    .plan-cost {
      border-left-color: #b89a62;
    }
    .access-copy-code.copied .access-copy-hint {
      background: #eaf3ee;
      color: #4f7f68;
    }
    .cost-type-pill.repair {
      background: #f7e9eb;
      color: #9b5862;
    }
    .cost-type-pill.periodic {
      background: #eaf3ee;
      color: #4f7f68;
    }
    .plan-reminder {
      border-color: #e2d4b4 !important;
      background: #fcfaf4 !important;
      color: #846f42 !important;
    }
    .plan-reminder-count {
      background: #b85e6a;
    }
    .metric-card.success,
    .dashboard-stat.ok {
      border-left-color: #5b9278;
    }
    .metric-card.danger,
    .dashboard-stat.danger {
      border-left-color: #b85e6a;
    }
    .dashboard-stat.warning {
      border-left-color: #b89a62;
    }
    .dashboard-stat.money {
      border-left-color: #5c8fb5;
    }
    .workflow-action.attention {
      border-color: #e2d4b4;
      background: #fcfaf4;
    }
    .workflow-action.attention .workflow-number {
      background: #b89a62;
    }
    .workflow-card.plan-card {
      border-top-color: #b89a62;
    }
    .plan-step.periodic {
      border-left-color: #5b9278;
    }
    .plan-step.warning {
      border-left-color: #b89a62;
    }
    .plan-step.danger {
      border-left-color: #b85e6a;
    }
    .alert.warning {
      background: #fcfaf4;
      color: #846f42;
      border-color: #e2d4b4;
    }
    .alert.danger {
      background: #fcf5f6;
      color: #9b5862;
      border-color: #e3c2c7;
    }
    .reminder-row {
      border-left-color: #b89a62;
      background: #fffdf8;
    }
    .reminder-row.overdue {
      border-left-color: #b85e6a;
      background: #fcf7f8;
    }
    .reminder-cell small {
      color: #846f42;
    }
    .reminder-row.overdue .reminder-cell small {
      color: #9b5862;
    }
    .status-pill.active {
      background: #5b9278;
    }
    .status-pill.expired {
      background: #b85e6a;
    }
    tr.access-active td {
      background: #f5faf7;
    }
    tr.access-expired td {
      background: #fcf7f8;
    }
    tr.access-active td:first-child,
    .access-table-wrap tr.access-active {
      border-left-color: #5b9278;
    }
    tr.access-expired td:first-child,
    .access-table-wrap tr.access-expired {
      border-left-color: #b85e6a;
    }
    .cost-category-card.repair,
    .cost-mini.repair {
      --category-color: #b86b78;
      border-left-color: #b86b78;
    }
    .cost-category-card.periodic,
    .cost-mini.periodic {
      --category-color: #5b9278;
      border-left-color: #5b9278;
    }
    .cost-category-card.oil,
    .cost-mini.oil {
      --category-color: #b89a62;
      border-left-color: #b89a62;
    }
    .cost-category-card.registry,
    .cost-mini.registry {
      --category-color: #5c8fb5;
      border-left-color: #5c8fb5;
    }
    .cost-type-pill.repair,
    .count-part.repair {
      background: #f7e9eb;
      color: #9b5862;
    }
    .cost-type-pill.periodic,
    .count-part.periodic {
      background: #eaf3ee;
      color: #4f7f68;
    }
    .count-part.oil {
      background: #f6f0e3;
      color: #846f42;
    }
    .count-part.registry {
      background: #edf4fa;
      color: #4e7c9f;
    }
    .cost-row-segment.repair {
      background: #b86b78;
    }
    .cost-row-segment.periodic {
      background: #5b9278;
    }
    .cost-row-segment.oil {
      background: #b89a62;
    }
    .cost-row-segment.registry {
      background: #5c8fb5;
    }
    .chart-segment.overdue,
    .legend-dot.overdue {
      background: #b85e6a;
    }
    .chart-segment.due,
    .legend-dot.due {
      background: #b89a62;
    }
    .chart-segment.ok,
    .legend-dot.ok {
      background: #5b9278;
    }
    .chart-segment.missing,
    .legend-dot.missing {
      background: #9aabbc;
    }
    .due-pill.overdue {
      background: #f7e9eb;
      color: #9b5862;
    }
    .due-pill.due {
      background: #f6f0e3;
      color: #846f42;
    }
    .due-pill.ok {
      background: #eaf3ee;
      color: #4f7f68;
    }
    .due-pill.missing {
      background: #f0f3f6;
      color: #6d7f91;
    }
    .plan-reminder,
    .workflow-action.attention,
    .alert.warning,
    .alert.danger,
    .reminder-row,
    .cost-type-pill,
    .count-part,
    .due-pill {
      box-shadow: none;
    }
    /* Pastel status palette: keep VietinBank blue for the brand and primary actions. */
    :root {
      --accent: #c97986;
      --accent-dark: #aa5866;
      --danger: #c98a96;
      --ok: #86b99e;
      --shadow: 0 10px 24px rgba(0, 43, 88, .055);
    }
    body {
      background: linear-gradient(180deg, #fcfdff 0, #f6f9fc 280px, #f1f6fa 100%);
    }
    .metric-card.success,
    .dashboard-stat.ok {
      border-left-color: #a6cbb5;
    }
    .metric-card.danger,
    .dashboard-stat.danger {
      border-left-color: #d8a6b0;
    }
    .dashboard-stat.warning {
      border-left-color: #dcc695;
    }
    .dashboard-stat.money {
      border-left-color: #9fc9e6;
    }
    .workflow-action.attention,
    .alert.warning {
      border-color: #eadcbf;
      background: #fdf9ef;
    }
    .workflow-action.attention .workflow-number {
      background: #dcc695;
      color: #806d43;
    }
    .workflow-card.plan-card,
    .plan-step.warning,
    .plan-cost {
      border-color: #dcc695;
    }
    .plan-step,
    .plan-step.registry,
    .plan-cost.registry {
      border-left-color: #a6cbb5;
    }
    .plan-step.periodic {
      border-left-color: #9fc9e6;
    }
    .plan-step.danger,
    .alert.danger,
    .reminder-row.overdue {
      border-left-color: #d8a6b0;
    }
    .alert.danger {
      background: #fcf2f4;
      color: #a96f79;
      border-color: #ebcbd1;
    }
    .reminder-row {
      border-left-color: #dcc695;
      background: #fffdf8;
    }
    .reminder-row.overdue {
      background: #fdf7f8;
    }
    .reminder-cell small {
      color: #927c4c;
    }
    .reminder-row.overdue .reminder-cell small {
      color: #a96f79;
    }
    .status-pill.active {
      background: #a6cbb5;
      color: #4f7860;
    }
    .status-pill.expired {
      background: #d8a6b0;
      color: #8f5d68;
    }
    .cost-category-card.repair,
    .cost-mini.repair {
      --category-color: #d8a6b0;
      border-left-color: #d8a6b0;
    }
    .cost-category-card.periodic,
    .cost-mini.periodic {
      --category-color: #9fc9e6;
      border-left-color: #9fc9e6;
    }
    .cost-category-card.oil,
    .cost-mini.oil {
      --category-color: #dcc695;
      border-left-color: #dcc695;
    }
    .cost-category-card.registry,
    .cost-mini.registry {
      --category-color: #a6cbb5;
      border-left-color: #a6cbb5;
    }
    .cost-type-pill.repair,
    .count-part.repair,
    .due-pill.overdue {
      background: #f8eaed;
      color: #a96f79;
    }
    .cost-type-pill.periodic,
    .count-part.periodic {
      background: #edf6fb;
      color: #547e9d;
    }
    .count-part.oil,
    .due-pill.due {
      background: #faf5e9;
      color: #927c4c;
    }
    .count-part.registry,
    .due-pill.ok {
      background: #eef8f1;
      color: #5d836c;
    }
    .due-pill.missing {
      background: #f1f4f7;
      color: #718396;
    }
    .cost-row-segment.repair {
      background: #d8a6b0;
    }
    .cost-row-segment.periodic {
      background: #9fc9e6;
    }
    .cost-row-segment.oil {
      background: #dcc695;
    }
    .cost-row-segment.registry {
      background: #a6cbb5;
    }
    .chart-segment.overdue,
    .legend-dot.overdue {
      background: #d8a6b0;
    }
    .chart-segment.due,
    .legend-dot.due {
      background: #dcc695;
    }
    .chart-segment.ok,
    .legend-dot.ok {
      background: #a6cbb5;
    }
    .chart-segment.missing,
    .legend-dot.missing {
      background: #b7c5d3;
    }
    label.required-field,
    .required-mark,
    .required-hint {
      color: #a96f79;
    }
    .required-input {
      border-color: #d8a6b0;
      box-shadow: 0 0 0 1px rgba(216, 166, 176, .16);
    }
    /* Clear pastel accents: stronger enough for quick scanning without harsh contrast. */
    :root {
      --accent: #cc6173;
      --accent-dark: #a94b5d;
      --danger: #c56b7b;
      --ok: #6eaa88;
    }
    .metric-card.success,
    .dashboard-stat.ok {
      border-left-color: #70aa89;
    }
    .metric-card.danger,
    .dashboard-stat.danger {
      border-left-color: #c97888;
    }
    .dashboard-stat.warning {
      border-left-color: #c6a05e;
    }
    .dashboard-stat.money {
      border-left-color: #78afd2;
    }
    .workflow-action.attention .workflow-number {
      background: #c6a05e;
      color: #725a2b;
    }
    .plan-step.periodic {
      border-left-color: #78afd2;
    }
    .plan-step.registry,
    .plan-cost.registry {
      border-left-color: #70aa89;
    }
    .plan-step.warning,
    .plan-cost {
      border-left-color: #c6a05e;
    }
    .plan-step.danger,
    .reminder-row.overdue {
      border-left-color: #c97888;
    }
    .status-pill.active {
      background: #70aa89;
      color: #315f47;
    }
    .status-pill.expired {
      background: #c97888;
      color: #7f3f4c;
    }
    .cost-category-card.repair,
    .cost-mini.repair {
      --category-color: #c97888;
      border-left-color: #c97888;
    }
    .cost-category-card.periodic,
    .cost-mini.periodic {
      --category-color: #78afd2;
      border-left-color: #78afd2;
    }
    .cost-category-card.oil,
    .cost-mini.oil {
      --category-color: #c6a05e;
      border-left-color: #c6a05e;
    }
    .cost-category-card.registry,
    .cost-mini.registry {
      --category-color: #70aa89;
      border-left-color: #70aa89;
    }
    .cost-type-pill.repair,
    .count-part.repair,
    .due-pill.overdue {
      background: #f5dfe4;
      color: #9d4f61;
    }
    .cost-type-pill.periodic,
    .count-part.periodic {
      background: #e0eff8;
      color: #477d9f;
    }
    .count-part.oil,
    .due-pill.due {
      background: #f7eedc;
      color: #80662e;
    }
    .count-part.registry,
    .due-pill.ok {
      background: #e2f1e8;
      color: #4f7f63;
    }
    .due-pill.missing {
      background: #e7eef4;
      color: #61788d;
    }
    .cost-row-segment.repair {
      background: #c97888;
    }
    .cost-row-segment.periodic {
      background: #78afd2;
    }
    .cost-row-segment.oil {
      background: #c6a05e;
    }
    .cost-row-segment.registry {
      background: #70aa89;
    }
    .chart-segment.overdue,
    .legend-dot.overdue {
      background: #c97888;
    }
    .chart-segment.due,
    .legend-dot.due {
      background: #c6a05e;
    }
    .chart-segment.ok,
    .legend-dot.ok {
      background: #70aa89;
    }
    .chart-segment.missing,
    .legend-dot.missing {
      background: #9aafc1;
    }
    label.required-field,
    .required-mark,
    .required-hint {
      color: #9d4f61;
    }
    .required-input {
      border-color: #c97888;
      box-shadow: 0 0 0 1px rgba(201, 120, 136, .18);
    }
    /* Stronger pastel palette for readability. */
    :root {
      --ink: #102c47;
      --muted: #5f7388;
      --line: #c8d9e6;
      --soft: #f3f7fb;
      --brand-soft: #e2eef8;
      --danger: #bd5e70;
      --ok: #5e9b7a;
    }
    .metric-card.success,
    .dashboard-stat.ok {
      border-left-color: #5e9b7a;
    }
    .metric-card.danger,
    .dashboard-stat.danger {
      border-left-color: #bd5e70;
    }
    .dashboard-stat.warning {
      border-left-color: #b88d3f;
    }
    .dashboard-stat.money {
      border-left-color: #5f9fc9;
    }
    .workflow-action.attention .workflow-number {
      background: #b88d3f;
      color: #624b1d;
    }
    .plan-step.periodic {
      border-left-color: #5f9fc9;
    }
    .plan-step.registry,
    .plan-cost.registry {
      border-left-color: #5e9b7a;
    }
    .plan-step.warning,
    .plan-cost {
      border-left-color: #b88d3f;
    }
    .plan-step.danger,
    .reminder-row.overdue {
      border-left-color: #bd5e70;
    }
    .status-pill.active {
      background: #5e9b7a;
      color: #fff;
    }
    .status-pill.expired {
      background: #bd5e70;
      color: #fff;
    }
    .cost-category-card.repair,
    .cost-mini.repair {
      --category-color: #bd5e70;
      border-left-color: #bd5e70;
    }
    .cost-category-card.periodic,
    .cost-mini.periodic {
      --category-color: #5f9fc9;
      border-left-color: #5f9fc9;
    }
    .cost-category-card.oil,
    .cost-mini.oil {
      --category-color: #b88d3f;
      border-left-color: #b88d3f;
    }
    .cost-category-card.registry,
    .cost-mini.registry {
      --category-color: #5e9b7a;
      border-left-color: #5e9b7a;
    }
    .cost-type-pill.repair,
    .count-part.repair,
    .due-pill.overdue {
      background: #f0d4da;
      color: #914052;
    }
    .cost-type-pill.periodic,
    .count-part.periodic {
      background: #d7e9f5;
      color: #3d6f91;
    }
    .count-part.oil,
    .due-pill.due {
      background: #f3e8cc;
      color: #735a22;
    }
    .count-part.registry,
    .due-pill.ok {
      background: #d8ece0;
      color: #416d53;
    }
    .due-pill.missing {
      background: #dfe9f0;
      color: #536d83;
    }
    .cost-row-segment.repair {
      background: #bd5e70;
    }
    .cost-row-segment.periodic {
      background: #5f9fc9;
    }
    .cost-row-segment.oil {
      background: #b88d3f;
    }
    .cost-row-segment.registry {
      background: #5e9b7a;
    }
    .chart-segment.overdue,
    .legend-dot.overdue {
      background: #bd5e70;
    }
    .chart-segment.due,
    .legend-dot.due {
      background: #b88d3f;
    }
    .chart-segment.ok,
    .legend-dot.ok {
      background: #5e9b7a;
    }
    .chart-segment.missing,
    .legend-dot.missing {
      background: #8ea7bb;
    }
    label.required-field,
    .required-mark,
    .required-hint {
      color: #914052;
    }
    .required-input {
      border-color: #bd5e70;
      box-shadow: 0 0 0 1px rgba(189, 94, 112, .2);
    }
    /* Restore the original high-contrast VietinBank status palette. */
    :root {
      --bg: #eef5fb;
      --ink: #09233f;
      --muted: #5d7288;
      --line: #cdddec;
      --soft: #f4f8fc;
      --brand-soft: #e7f1fb;
      --accent: #ed1c24;
      --accent-dark: #b90f17;
      --danger: #c62828;
      --ok: #107c41;
      --shadow: 0 18px 42px rgba(0, 43, 88, .10);
    }
    body {
      background: linear-gradient(180deg, #f7fbff 0, #eef5fb 280px, #eaf1f8 100%);
    }
    .topbar {
      border-bottom-width: 4px;
      box-shadow: 0 12px 30px rgba(0, 54, 108, .18);
    }
    .btn.primary {
      background: linear-gradient(180deg, #0068bd, #005baa);
    }
    .btn.primary:hover {
      background: linear-gradient(180deg, #005baa, #00457f);
    }
    .login-page {
      background:
        linear-gradient(120deg, rgba(255,255,255,.08) 0 18%, transparent 18% 100%),
        linear-gradient(135deg, #003f78 0%, #005baa 48%, #0067b1 68%, #b3133b 100%);
    }
    .login-visual {
      background:
        linear-gradient(160deg, rgba(0,69,127,.98), rgba(0,91,170,.96) 58%, rgba(237,28,36,.88)),
        #00457f;
    }
    .plan-reminder {
      border-color: #f2d27a !important;
      background: #fff7df !important;
      color: #946200 !important;
    }
    .plan-reminder-count {
      background: #c62828;
    }
    label.required-field,
    .required-hint {
      color: #b3133b;
    }
    .required-mark {
      color: #e0194d;
    }
    .required-input {
      border-color: #e0194d;
      box-shadow: 0 0 0 1px rgba(224, 25, 77, .12);
    }
    .metric-card.success,
    .dashboard-stat.ok {
      border-left-color: #159957;
    }
    .metric-card.danger,
    .dashboard-stat.danger {
      border-left-color: #c62828;
    }
    .dashboard-stat.warning {
      border-left-color: #d88900;
    }
    .dashboard-stat.money {
      border-left-color: #2563eb;
    }
    .workflow-action.attention {
      border-color: #f2d27a;
      background: #fff9e9;
    }
    .workflow-action.attention .workflow-number {
      background: #d88900;
      color: #fff;
    }
    .workflow-card.plan-card {
      border-top-color: #d88900;
    }
    .plan-step {
      border-left-color: #0b75bb;
    }
    .plan-step.periodic {
      border-left-color: #15965f;
    }
    .plan-step.registry,
    .plan-cost.registry {
      border-left-color: #0b75bb;
    }
    .plan-step.warning,
    .plan-cost {
      border-left-color: #d88900;
    }
    .plan-step.danger,
    .reminder-row.overdue {
      border-left-color: #e0194d;
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
    .reminder-row {
      border-left-color: #d88900;
      background: #fffdf7;
    }
    .reminder-row.overdue {
      background: #fffafa;
    }
    .reminder-cell small {
      color: #946200;
    }
    .reminder-row.overdue .reminder-cell small {
      color: #b3133b;
    }
    .status-pill.active {
      background: #159957;
      color: #fff;
    }
    .status-pill.expired {
      background: #d91f3f;
      color: #fff;
    }
    tr.access-active td {
      background: #f6fffa;
    }
    tr.access-expired td {
      background: #fff6f8;
    }
    tr.access-active td:first-child,
    .access-table-wrap tr.access-active {
      border-left-color: #159957;
    }
    tr.access-expired td:first-child,
    .access-table-wrap tr.access-expired {
      border-left-color: #d91f3f;
    }
    .access-copy-code.copied .access-copy-hint {
      background: #dff7e9;
      color: #0b6f3d;
    }
    .cost-category-card.repair,
    .cost-mini.repair {
      --category-color: #d91f3f;
      border-left-color: #d91f3f;
    }
    .cost-category-card.periodic,
    .cost-mini.periodic {
      --category-color: #159957;
      border-left-color: #159957;
    }
    .cost-category-card.oil,
    .cost-mini.oil {
      --category-color: #d88900;
      border-left-color: #d88900;
    }
    .cost-category-card.registry,
    .cost-mini.registry {
      --category-color: #0b75bb;
      border-left-color: #0b75bb;
    }
    .cost-type-pill.repair,
    .count-part.repair,
    .due-pill.overdue {
      background: #ffe5e9;
      color: #b3133b;
    }
    .cost-type-pill.periodic,
    .count-part.periodic {
      background: #dff7e9;
      color: #0b6f3d;
    }
    .count-part.oil,
    .due-pill.due {
      background: #fff3d9;
      color: #9a6400;
    }
    .count-part.registry {
      background: #e5f2fb;
      color: #075f99;
    }
    .due-pill.ok {
      background: #dff7e9;
      color: #107c41;
    }
    .due-pill.missing {
      background: #eef2f6;
      color: #637386;
    }
    .cost-row-segment.repair {
      background: #e0194d;
    }
    .cost-row-segment.periodic {
      background: #15965f;
    }
    .cost-row-segment.oil {
      background: #d88900;
    }
    .cost-row-segment.registry {
      background: #0b75bb;
    }
    .chart-segment.overdue,
    .legend-dot.overdue {
      background: #c62828;
    }
    .chart-segment.due,
    .legend-dot.due {
      background: #d88900;
    }
    .chart-segment.ok,
    .legend-dot.ok {
      background: #107c41;
    }
    .chart-segment.missing,
    .legend-dot.missing {
      background: #93a4b7;
    }
    /* Swap the periodic-maintenance and inspection colors. */
    .plan-step.periodic {
      border-left-color: #0b75bb;
    }
    .plan-step.registry,
    .plan-cost.registry {
      border-left-color: #15965f;
    }
    .cost-category-card.periodic,
    .cost-mini.periodic {
      --category-color: #0b75bb;
      border-left-color: #0b75bb;
    }
    .cost-category-card.registry,
    .cost-mini.registry {
      --category-color: #159957;
      border-left-color: #159957;
    }
    .cost-type-pill.periodic,
    .count-part.periodic {
      background: #e5f2fb;
      color: #075f99;
    }
    .count-part.registry {
      background: #dff7e9;
      color: #0b6f3d;
    }
    .cost-row-segment.periodic {
      background: #0b75bb;
    }
    .cost-row-segment.registry {
      background: #15965f;
    }
    /* Mobile layout: keep controls readable and prevent desktop grids from
       forcing horizontal scrolling on phones and small tablets. */
    @media (max-width: 820px) {
      .page {
        width: calc(100% - 24px);
        margin: 14px auto 28px;
      }
      .dashboard-module-switcher {
        justify-content: stretch;
      }
      .dashboard-module-switcher .toolbar,
      .center-filter-toolbar {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        width: 100% !important;
        flex-wrap: unset;
        gap: 8px;
      }
      .dashboard-module-switcher .toolbar .btn,
      .center-filter-toolbar .btn {
        width: 100%;
        min-width: 0;
        min-height: 42px;
        padding: 9px 8px;
        font-size: 13px;
        line-height: 1.2;
        white-space: normal;
      }
      .center-filter-toolbar .badge {
        grid-column: 1 / -1;
        justify-self: start;
      }
      .dashboard-list-controls {
        display: grid;
        grid-template-columns: 1fr;
        align-items: stretch;
      }
      .dashboard-list-search,
      .dashboard-list-size {
        width: 100%;
        min-width: 0;
      }
      .dashboard-list-search .vehicle-search {
        width: 100%;
        min-width: 0;
      }
      .dashboard-list-size {
        justify-content: space-between;
        white-space: normal;
      }
      .cost-total-card strong {
        font-size: clamp(24px, 7.2vw, 32px);
        overflow-wrap: anywhere;
      }
      .maintenance-toolbar select {
        width: 100%;
        min-width: 0;
      }
      .public-record-table-wrap {
        max-width: 100%;
        overflow-x: auto;
      }
    }
    @media (max-width: 480px) {
      .page {
        width: calc(100% - 16px);
        margin-top: 10px;
      }
      .topbar {
        padding: 10px;
      }
      .brand-name {
        font-size: 19px;
      }
      .brand-kicker {
        font-size: 8px;
      }
      .panel {
        padding: 11px;
        border-radius: 8px;
      }
      .dashboard-stats {
        gap: 7px;
      }
      .dashboard-stat {
        min-height: 78px;
        padding: 9px;
      }
      .dashboard-stat strong {
        font-size: 20px;
      }
      .cost-total-card strong {
        font-size: 23px;
      }
      .center-filter-toolbar .btn,
      .dashboard-module-switcher .toolbar .btn {
        min-height: 40px;
        font-size: 12px;
      }
      .dashboard-modal-backdrop {
        padding: 8px;
      }
      .dashboard-modal-head {
        padding: 13px;
      }
      .dashboard-modal-body {
        padding: 9px;
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
        {% if plan_alerts %}
          <a class="plan-reminder" href="{{ url_for('maintenance_alerts') }}" title="Xem danh sách kế hoạch dự kiến chưa cập nhật thực tế">
            Nhắc việc <span class="plan-reminder-count">{{ plan_alerts|length }}</span>
          </a>
        {% endif %}
        <a class="{{ 'active' if active == 'dashboard' else '' }}" href="{{ url_for('dashboard') }}">Dashboard</a>
        <a class="{{ 'active' if active == 'detail' else '' }}" href="{{ url_for('detail') }}">Chi tiết</a>
        {% if can_manage_data %}
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
        is_center_admin=is_area_admin(),
        management_label=current_area_label(),
        can_manage_data=can_manage_data(),
        plan_alerts=pending_maintenance_plan_alerts(),
        messages=flash_messages(),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        limited, retry_after = login_is_rate_limited()
        if limited:
            flash(f"Quá nhiều lần đăng nhập sai. Vui lòng thử lại sau {retry_after} giây.", "error")
            return redirect(url_for("login"))
        code = request.form.get("code", "").strip()
        if ADMIN_KEY and hmac.compare_digest(code, ADMIN_KEY):
            session["access_info"] = {"code": ADMIN_KEY, "bien_so": "ALL", "cap_time": None}
            touch_last_activity()
            clear_login_failures()
            audit_log("login", "Đăng nhập quản trị")
            return redirect(url_for("dashboard"))

        init_db()
        for area_code in AREA_ADMIN_SPECS:
            if area_admin_key_matches(area_code, code):
                session["access_info"] = {
                    "code": code,
                    "bien_so": "ALL",
                    "cap_time": None,
                    "role": "area_admin",
                    "area": area_code,
                    "center": area_code,
                }
                touch_last_activity()
                clear_login_failures()
                audit_log("login", f"Đăng nhập quản trị {CENTER_FILTER_LABELS[area_code]}")
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
                clear_login_failures()
                audit_log("login", f"Đăng nhập mã truy cập cho {row.iloc[0]['BienSo']}")
                return redirect(url_for("dashboard"))

        register_login_failure()

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
                <h1>Hệ thống quản lý xe</h1>
                <p>Quản lý phương tiện, lịch bảo dưỡng, đăng kiểm và chi phí vận hành.</p>
              </div>
              <div class="login-stats">
                <div class="login-stat"><strong>Truy cập hệ thống</strong><span>Theo thời hạn được cấp</span></div>
                <div class="login-stat"><strong>Báo cáo vận hành</strong><span>Xuất lịch sử bảo dưỡng</span></div>
              </div>
            </section>
            <form class="login-card" method="post">
              {{ messages|safe }}
              <h1>Đăng nhập</h1>
              <p>Nhập mã truy cập để tiếp tục.</p>
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
    data = load_data(["xe", "history", "next", "oil", "registration"])
    if can_manage_data():
        default_center = current_center_scope() if is_area_admin() else CENTER_FILTER_ALL
        center_filter = normalize_center_filter(request.args.get("center") or default_center)
        data["xe"] = center_filtered_vehicle_df(scoped_vehicle_df(data["xe"]), center_filter)
    else:
        center_filter = CENTER_FILTER_ALL
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
    for status_field, status_title in (("registry", "Đăng kiểm"), ("maintenance", "Bảo dưỡng")):
        for status_key, status_label in (("overdue", "Quá hạn"), ("due", "Sắp đến hạn"), ("ok", "Còn hạn"), ("missing", "Chưa cập nhật")):
            if status_field == "registry" and status_key == "overdue":
                continue
            dashboard_modal_groups.append(
                {
                    "key": f"{status_field}-{status_key}",
                    "title": f"{status_title}: {status_label}",
                    "rows": [row for row in rows if row[status_field].get("key") == status_key],
                    "unit": "xe",
                }
            )
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
    dashboard_pagination_pages = pagination_items(dashboard_page, dashboard_total_pages)
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
        "periodic": "#0b75bb",
        "oil": "#d88900",
        "registry": "#159957",
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

        {% if root_admin %}
          <section class="dashboard-module-switcher">
            <div class="toolbar">
              {% for center_code, center_label in center_filters.items() %}
                <a class="btn {{ 'primary' if center_filter == center_code else '' }}" href="{{ url_for('dashboard', center=center_code, cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, q=dashboard_query) }}">{{ center_label }}</a>
              {% endfor %}
            </div>
          </section>
        {% else %}
          <section class="dashboard-module-switcher">
            <span class="badge">{{ center_filter_label }}</span>
          </section>
        {% endif %}

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
              <a class="cost-tab {{ 'active' if cost_stats.period == 'month' else '' }}" href="{{ url_for('dashboard', center=center_filter, cost_period='month', cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, q=dashboard_query) }}">Tháng</a>
              <a class="cost-tab {{ 'active' if cost_stats.period == 'quarter' else '' }}" href="{{ url_for('dashboard', center=center_filter, cost_period='quarter', cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, q=dashboard_query) }}">Quý</a>
              <a class="cost-tab {{ 'active' if cost_stats.period == 'year' else '' }}" href="{{ url_for('dashboard', center=center_filter, cost_period='year', cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, q=dashboard_query) }}">Năm</a>
            </div>
            <form method="get" action="{{ url_for('dashboard') }}">
              <input type="hidden" name="center" value="{{ center_filter }}">
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
                    <button class="legend-item dashboard-status-legend" type="button" data-dashboard-modal="registry-{{ segment.key }}" aria-label="Xem xe đăng kiểm {{ segment.label }}">
                      <span class="legend-label"><span class="legend-dot {{ segment.key }}"></span>{{ segment.label }}</span>
                      <strong class="legend-value">{{ segment.value }}</strong>
                    </button>
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
                    <button class="legend-item dashboard-status-legend" type="button" data-dashboard-modal="maintenance-{{ segment.key }}" aria-label="Xem xe bảo dưỡng {{ segment.label }}">
                      <span class="legend-label"><span class="legend-dot {{ segment.key }}"></span>{{ segment.label }}</span>
                      <strong class="legend-value">{{ segment.value }}</strong>
                    </button>
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
              <input type="hidden" name="center" value="{{ center_filter }}">
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
                <a class="btn" href="{{ url_for('dashboard', center=center_filter, cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page) }}">Xóa lọc</a>
              {% endif %}
            </form>
            <span class="subtle">
              Hiển thị {{ dashboard_start + 1 if dashboard_list_total else 0 }}-{{ dashboard_end }} / {{ dashboard_list_total }} xe
            </span>
            <form class="dashboard-list-size" method="get">
              <input type="hidden" name="center" value="{{ center_filter }}">
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
          <div class="table-wrap dashboard-vehicle-table">
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
                <a class="btn {{ 'disabled' if dashboard_page <= 1 else '' }}" data-dashboard-preserve-scroll href="{{ url_for('dashboard', center=center_filter, cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page, dashboard_page=1, q=dashboard_query) }}">&laquo; Đầu</a>
                <a class="btn {{ 'disabled' if dashboard_page <= 1 else '' }}" data-dashboard-preserve-scroll href="{{ url_for('dashboard', center=center_filter, cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page, dashboard_page=dashboard_page - 1, q=dashboard_query) }}">&lsaquo; Trước</a>
                {% for p in dashboard_pagination_pages %}
                  {% if p == 'ellipsis' %}
                    <span class="pagination-ellipsis" aria-hidden="true">&hellip;</span>
                  {% else %}
                    <a class="btn {{ 'active' if p == dashboard_page else '' }}" data-dashboard-preserve-scroll href="{{ url_for('dashboard', center=center_filter, cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page, dashboard_page=p, q=dashboard_query) }}">{{ p }}</a>
                  {% endif %}
                {% endfor %}
                <a class="btn {{ 'disabled' if dashboard_page >= dashboard_total_pages else '' }}" data-dashboard-preserve-scroll href="{{ url_for('dashboard', center=center_filter, cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page, dashboard_page=dashboard_page + 1, q=dashboard_query) }}">Sau &rsaquo;</a>
                <a class="btn {{ 'disabled' if dashboard_page >= dashboard_total_pages else '' }}" data-dashboard-preserve-scroll href="{{ url_for('dashboard', center=center_filter, cost_period=cost_stats.period, cost_month=cost_month, cost_quarter=cost_quarter, cost_year=cost_year, dashboard_per_page=dashboard_per_page, dashboard_page=dashboard_total_pages, q=dashboard_query) }}">Cuối &raquo;</a>
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
        dashboard_pagination_pages=dashboard_pagination_pages,
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
        root_admin=is_admin(),
        center_admin=is_center_admin(),
        center_filter=center_filter,
        center_filter_label=CENTER_FILTER_LABELS.get(center_filter, CENTER_FILTER_LABELS[CENTER_FILTER_ALL]),
        center_filters=CENTER_FILTER_LABELS,
    )
    return page(content, title="Quản lý xe Vietinbank", active="dashboard")


@app.route("/")
def home():
    if current_access():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/maintenance-alerts")
@login_required
def maintenance_alerts():
    alerts = pending_maintenance_plan_alerts()
    content = render_template_string(
        r"""
        <section class="hero">
          <div>
            <h1>Nhắc việc cần cập nhật</h1>
            <p class="subtle">Danh sách các lịch bảo dưỡng hoặc đăng kiểm đã đến hạn theo dữ liệu mới nhất.</p>
          </div>
          <div class="toolbar"><span class="badge">{{ alerts|length }} công việc</span><a class="btn" href="{{ url_for('dashboard') }}">Về Dashboard</a></div>
        </section>
        <section class="panel">
          <div class="section-head"><div><h2>Danh sách công việc cần làm</h2><p class="subtle" style="margin:3px 0 0">Mở hồ sơ từng xe để cập nhật bản ghi thực tế tương ứng.</p></div></div>
          <div hidden aria-hidden="true" id="legacy-maintenance-alert-label">Danh sách kế hoạch dự kiến chưa cập nhật <span data-alert-complete-open></span><span id="alert-complete-modal"></span></div>
          {% if alerts %}
            <div class="reminder-list">
              {% for alert in alerts %}
                <article class="reminder-row {{ 'overdue' if alert.overdue_days > 0 else '' }}">
                  <div class="reminder-cell reminder-vehicle"><span>Xe</span><strong>{{ alert.plate }}</strong><small>{{ alert.car_type or 'Chưa cập nhật' }} · {{ alert.center_label }}</small></div>
                  <div class="reminder-cell"><span>Hạng mục</span><strong>{{ alert.plan_type_label }}</strong><small>{{ 'Quá hạn ' ~ alert.overdue_days ~ ' ngày' if alert.overdue_days > 0 else 'Đến hạn hôm nay' }}</small></div>
                  <div class="reminder-cell"><span>Lịch dự kiến</span><strong>{{ alert.planned_date }}</strong><small>{{ alert.content }}</small></div>
                  {% if can_manage_data %}<a class="btn primary reminder-action" href="{{ url_for('admin_data', tab='maintenance', plate=alert.plate) }}">Cập nhật</a>{% else %}<a class="btn reminder-action" href="{{ url_for('detail', plate=alert.plate) }}">Xem xe</a>{% endif %}
                </article>
              {% endfor %}
            </div>
          {% else %}<div class="empty">Hiện không có lịch nào đến hạn cần cập nhật.</div>{% endif %}
        </section>
        """,
        alerts=alerts,
        can_manage_data=can_manage_data(),
    )
    return page(content, title="Nhắc việc cần cập nhật", active="alerts")

    # Kept below for backwards compatibility with old deployment snapshots.
    content = render_template_string(
        r"""
        <section class="hero">
          <div>
            <h1>Kế hoạch dự kiến chưa cập nhật</h1>
            <p class="subtle">Danh sách các kế hoạch dự kiến đã đến hạn hoặc quá hạn nhưng chưa ghi nhận trạng thái thực tế.</p>
          </div>
          <div class="toolbar">
            <span class="badge">{{ alerts|length }} kế hoạch</span>
            <a class="btn" href="{{ url_for('dashboard') }}">Về Dashboard</a>
          </div>
        </section>

        <section class="panel">
          <div class="section-head">
            <div>
              <h2>Danh sách kế hoạch dự kiến chưa cập nhật</h2>
              <p class="subtle" style="margin:3px 0 0">Chọn “Cập nhật thực tế” ở từng dòng để ghi nhận ngày thực hiện, nội dung và số tiền.</p>
            </div>
          </div>
          {% if alerts %}
            <div class="reminder-list">
              {% for alert in alerts %}
                <article class="reminder-row {{ 'overdue' if alert.overdue_days > 0 else '' }}">
                  <div class="reminder-cell reminder-vehicle">
                    <span>Xe</span>
                    <strong>{{ alert.plate }}</strong>
                    <small>{{ alert.car_type or 'Chưa cập nhật' }} · {{ alert.center_label }}</small>
                  </div>
                  <div class="reminder-cell">
                    <span>Loại kế hoạch</span>
                    <strong>{{ alert.plan_type_label }}</strong>
                    <small>Chưa cập nhật thực tế · {{ 'quá hạn ' ~ alert.overdue_days ~ ' ngày' if alert.overdue_days > 0 else 'đến hạn hôm nay' }}</small>
                  </div>
                  <div class="reminder-cell">
                    <span>Kế hoạch dự kiến</span>
                    <strong>{{ alert.planned_date }}</strong>
                    <small>{{ alert.content or 'Chưa nhập nội dung' }}</small>
                  </div>
                  {% if can_manage_data %}
                    <button
                      class="btn primary reminder-action"
                      type="button"
                      data-alert-complete-open
                      data-plate="{{ alert.plate }}"
                      data-planned-date="{{ alert.planned_date_raw }}"
                      data-plan-type="{{ alert.plan_type }}"
                      data-content="{{ alert.content }}"
                    >Cập nhật thực tế</button>
                  {% else %}
                    <a class="btn reminder-action" href="{{ url_for('detail', plate=alert.plate) }}">Xem xe</a>
                  {% endif %}
                </article>
              {% endfor %}
            </div>
          {% else %}
            <div class="empty">Hiện không có kế hoạch dự kiến nào cần cập nhật thực tế.</div>
          {% endif %}
        </section>
        {% if can_manage_data %}
          <div class="inline-modal-backdrop" id="alert-complete-modal" hidden>
            <div class="inline-modal" role="dialog" aria-modal="true" aria-labelledby="alert-complete-modal-title">
              <div class="inline-modal-head">
                <h3 id="alert-complete-modal-title">Cập nhật đã thực hiện</h3>
                <button class="dashboard-modal-close" type="button" data-alert-complete-close aria-label="Đóng">×</button>
              </div>
              <div class="inline-modal-body">
                <form method="post" action="{{ url_for('admin_data') }}">
                  <input type="hidden" name="action" value="complete_plan">
                  <input type="hidden" name="plate" id="alert-complete-plate">
                  <input type="hidden" name="return_to" value="alerts">
                  <div class="inline-modal-grid">
                    <div class="form-row">
                      <div class="field">
                        <label class="required-field" for="alert-complete-date">Ngày thực hiện <span class="required-mark" aria-hidden="true">*</span></label>
                        <input id="alert-complete-date" class="required-input" name="actual_date" type="date" max="{{ today }}" value="{{ today }}" required>
                      </div>
                      <div class="field">
                        <label class="required-field" for="alert-complete-cost">Số tiền thực tế <span class="required-mark" aria-hidden="true">*</span></label>
                        <input id="alert-complete-cost" class="money-input required-input" name="cost" inputmode="numeric" autocomplete="off" placeholder="VD: 1.500.000" required>
                      </div>
                    </div>
                    <div class="required-hint">* Bắt buộc nhập. Ngày thực hiện đang mặc định là hôm nay, có thể thay đổi nếu cần.</div>
                    <div class="field">
                      <label for="alert-complete-type">Loại thực hiện</label>
                      <select id="alert-complete-type" name="cost_type">
                        <option value="periodic">Bảo dưỡng định kỳ</option>
                        <option value="repair">Sửa chữa</option>
                      </select>
                    </div>
                    <div class="field">
                      <label for="alert-complete-content">Nội dung thực tế</label>
                      <textarea id="alert-complete-content" name="content" required></textarea>
                    </div>
                    <div class="inline-modal-actions">
                      <button class="btn" type="button" data-alert-complete-close>Hủy</button>
                      <button class="btn primary" type="submit">Cập nhật đã thực hiện</button>
                    </div>
                  </div>
                </form>
              </div>
            </div>
          </div>
          <script>
            (() => {
              const modal = document.getElementById("alert-complete-modal");
              if (!modal) return;
              const plate = modal.querySelector("#alert-complete-plate");
              const date = modal.querySelector("#alert-complete-date");
              const cost = modal.querySelector("#alert-complete-cost");
              const type = modal.querySelector("#alert-complete-type");
              const content = modal.querySelector("#alert-complete-content");
              const formatMoney = (value) => {
                const digits = value.replace(/[^\d]/g, "");
                return digits ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".") : "";
              };
              const open = (button) => {
                plate.value = button.dataset.plate || "";
                date.min = button.dataset.plannedDate || "";
                date.value = "{{ today }}";
                type.value = button.dataset.planType || "periodic";
                content.value = button.dataset.content || "";
                cost.value = "";
                modal.hidden = false;
                requestAnimationFrame(() => modal.classList.add("open"));
              };
              const close = () => {
                modal.classList.remove("open");
                modal.hidden = true;
              };
              document.querySelectorAll("[data-alert-complete-open]").forEach((button) => button.addEventListener("click", () => open(button)));
              modal.querySelectorAll("[data-alert-complete-close]").forEach((button) => button.addEventListener("click", close));
              modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
              document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !modal.hidden) close(); });
              cost.addEventListener("input", () => {
                cost.value = formatMoney(cost.value);
                cost.setSelectionRange(cost.value.length, cost.value.length);
              });
            })();
          </script>
        {% endif %}
        """,
        alerts=alerts,
        can_manage_data=can_manage_data(),
        today=now_vn().strftime("%Y-%m-%d"),
    )
    return page(content, title="Kế hoạch dự kiến chưa cập nhật", active="alerts")


def render_vehicle_detail_workspace(data, selected, center_filter=CENTER_FILTER_ALL):
    vehicles = data.get("xe", pd.DataFrame())
    car_view = vehicles[vehicles["Biển số"].astype(str) == selected] if not vehicles.empty else pd.DataFrame()
    car = car_view.iloc[0].to_dict() if not car_view.empty else {}
    labels = plate_label_map(vehicles)
    history = data.get("history", pd.DataFrame())
    history_view = history[history["Biển số"].astype(str) == selected].copy() if not history.empty else pd.DataFrame()
    history_rows = build_history_rows(history_view)
    periodic_rows = [row for row in history_rows if row["cost_type"] == COST_TYPE_PERIODIC]
    repair_rows = [row for row in history_rows if row["cost_type"] == COST_TYPE_REPAIR]
    oil_rows = build_oil_rows(data.get("oil", pd.DataFrame()), selected)
    registration_rows = build_registration_rows(data.get("registration", pd.DataFrame()), selected)
    schedule = maintenance_schedule(history, selected)
    latest_registration = registration_rows[0] if registration_rows else None
    registry_state = due_state(f"{latest_registration['due_month_raw']}-01") if latest_registration else due_state("")
    total_cost = sum(parse_money_value(row.get("cost_raw")) for row in history_rows)
    total_cost += sum(parse_money_value(row.get("cost_raw")) for row in oil_rows)
    total_cost += sum(parse_money_value(row.get("cost_raw")) for row in registration_rows)
    vehicle_options = []
    for plate in sorted(safe_unique(vehicles, "Biển số")):
        vehicle_options.append({"plate": plate, "label": labels.get(plate, plate)})
    selected_label = labels.get(selected, selected)
    content = render_template_string(
        r"""
        <style>
          .public-detail { display:grid; gap:16px; }
          .public-vehicle-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; }
          .public-vehicle-item { padding:12px; border:1px solid #c8d9e6; border-radius:10px; background:#fbfdff; }
          .public-vehicle-item small { display:block; color:#5d7288; margin-bottom:5px; }
          .public-vehicle-item strong { color:#09233f; font-size:16px; }
          .public-data-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; align-items:stretch; }
          .public-data-zone { display:grid; align-content:start; gap:12px; padding:16px; border:1px solid #c8d9e6; border-radius:12px; border-top:5px solid #0b75bb; background:#fff; min-width:0; }
          .public-data-zone.periodic { border-top-color:#0b75bb; }
          .public-data-zone.repair { border-top-color:#b86b78; }
          .public-data-zone.registration { border-top-color:#159957; }
          .public-data-zone.oil { border-top-color:#b89a62; }
          .public-data-zone-head { display:flex; align-items:center; justify-content:space-between; gap:10px; }
          .public-data-zone-actions { display:flex; align-items:center; gap:8px; flex-shrink:0; }
          .public-data-zone-head h2 { margin:0 0 4px; font-size:19px; }
          .public-data-zone-head p { margin:0; color:#5d7288; font-size:13px; line-height:1.4; }
          .public-data-zone-badge { flex:0 0 auto; padding:6px 9px; border-radius:999px; background:#edf5fb; color:#075f99; font-size:12px; font-weight:800; white-space:nowrap; }
          .public-data-zone.repair .public-data-zone-badge { background:#f7e9eb; color:#9b5862; }
          .public-data-zone.registration .public-data-zone-badge { background:#eaf3ee; color:#4f7f68; }
          .public-data-zone.oil .public-data-zone-badge { background:#f6f0e3; color:#846f42; }
          .public-schedule-values { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; }
          .public-schedule-value { padding:10px; border:1px solid #d7e3ee; border-radius:9px; background:#fbfdff; }
          .public-schedule-value small { display:block; color:#5d7288; margin-bottom:4px; }
          .public-schedule-value strong { color:#09233f; font-size:16px; }
          .public-schedule-value.full { grid-column:1/-1; }
          .public-record-heading { margin-top:2px; color:#315d82; font-size:13px; font-weight:800; }
          .public-record-table { width:100%; border-collapse:collapse; min-width:520px; font-size:13px; }
          .public-record-table th, .public-record-table td { padding:9px; border-bottom:1px solid #e2ebf3; text-align:left; vertical-align:top; }
          .public-record-table th { color:#315d82; background:#f1f6fb; }
          .public-record-table-wrap { overflow:auto; max-height:300px; border:1px solid #d6e2ee; border-radius:8px; }
          .public-month { cursor:help; border-bottom:1px dotted #55718e; }
          .public-vehicle-picker { display:grid; gap:6px; align-items:stretch; min-width:min(420px, 100%); }
          .public-vehicle-picker .combo { width:100%; min-width:360px; }
          .public-vehicle-picker .combo-empty { padding:10px 12px; color:#55718e; font-size:13px; }
          @media (max-width:900px) { .public-vehicle-grid,.public-data-grid { grid-template-columns:1fr; } }
          @media (max-width:620px) { .public-schedule-values { grid-template-columns:1fr; } .public-vehicle-picker { min-width:0; width:100%; } .public-vehicle-picker .combo { min-width:0; } }
        </style>
        <section class="hero">
          <div><h1>Chi tiết xe</h1></div>
          <div class="toolbar public-vehicle-picker">
            <label for="public-plate-search">Chọn xe</label>
            <div class="combo" id="public-plate-combo">
              <input id="public-plate-search" value="{{ selected_label }}" placeholder="Tìm biển số hoặc loại xe" autocomplete="off" aria-controls="public-plate-options">
              <button class="combo-toggle" type="button" aria-label="Mở danh sách xe">▼</button>
              <div class="combo-menu" id="public-plate-options">
                {% for option in vehicle_options %}<button class="combo-option" type="button" data-label="{{ option.label }}" data-url="{{ url_for('detail', plate=option.plate, center=center_filter) }}">{{ option.label }}</button>{% endfor %}
                <div class="combo-empty" hidden>Không tìm thấy xe phù hợp.</div>
              </div>
            </div>
          </div>
        </section>
        <script>
          (() => {
            const combo = document.getElementById("public-plate-combo");
            const search = document.getElementById("public-plate-search");
            const toggle = combo?.querySelector(".combo-toggle");
            const options = Array.from(document.querySelectorAll("#public-plate-options .combo-option"));
            const empty = combo?.querySelector(".combo-empty");
            if (!combo || !search || !toggle) return;
            const committedLabel = search.value;
            const openMenu = () => combo.classList.add("open");
            const closeMenu = () => combo.classList.remove("open");
            const filterOptions = (query = "") => {
              const normalized = query.trim().toLowerCase();
              let visible = 0;
              options.forEach((option) => {
                const matches = !normalized || option.dataset.label.toLowerCase().includes(normalized);
                option.hidden = !matches;
                if (matches) visible += 1;
              });
              if (empty) empty.hidden = visible > 0;
            };
            const restore = () => {
              search.value = committedLabel;
              filterOptions("");
              closeMenu();
            };
            toggle.addEventListener("click", () => {
              if (combo.classList.contains("open")) restore();
              else { filterOptions(""); openMenu(); search.focus(); }
            });
            search.addEventListener("focus", () => { search.select(); filterOptions(""); openMenu(); });
            search.addEventListener("input", () => { filterOptions(search.value); openMenu(); });
            options.forEach((option) => option.addEventListener("click", () => { window.location.href = option.dataset.url; }));
            search.addEventListener("keydown", (event) => { if (event.key === "Escape") restore(); });
            document.addEventListener("click", (event) => { if (!combo.contains(event.target)) restore(); });
          })();
        </script>
        <section class="panel public-detail">
          <div class="section-head"><div><h2>{{ selected }}</h2><p class="subtle">{{ car.get('Loại xe', 'Chưa cập nhật') }}</p></div><div class="toolbar"><a class="btn" href="{{ url_for('detail', center=center_filter) }}">Quay lại danh sách xe</a></div></div>
          <div class="public-vehicle-grid"><div class="public-vehicle-item"><small>Loại xe</small><strong>{{ car.get('Loại xe', 'Chưa cập nhật') }}</strong></div><div class="public-vehicle-item"><small>Năm sản xuất</small><strong>{{ car.get('Năm sản xuất', 'Chưa cập nhật') }}</strong></div><div class="public-vehicle-item"><small>Trạng thái</small><strong>{{ car.get('Trạng thái', 'Chưa cập nhật') }}</strong></div><div class="public-vehicle-item"><small>Số lần bảo dưỡng</small><strong>{{ periodic_rows|length }}</strong></div><div class="public-vehicle-item"><small>Tổng chi phí</small><strong>{{ format_vnd(total_cost) }}</strong></div></div>
        </section>
        <section class="public-data-grid">
          <article class="public-data-zone periodic">
            <div class="public-data-zone-head">
              <div><h2>Bảo dưỡng định kỳ</h2></div>
              <div class="public-data-zone-actions">{% if can_manage_data %}<a class="btn primary" href="{{ url_for('admin_data', tab='maintenance', plate=selected, center=center_filter, focus='periodic') }}">Cập nhật</a>{% endif %}<span class="public-data-zone-badge">{{ periodic_rows|length }} bản ghi</span></div>
            </div>
            <div class="public-schedule-values">
              <div class="public-schedule-value"><small>Lần gần nhất</small><strong>{{ schedule.latest_display }}</strong></div>
              <div class="public-schedule-value"><small>Lịch tiếp theo</small><strong>{% if schedule.next_date %}<span class="public-month" title="Ngày dự kiến: {{ schedule.next_tooltip }}">{{ schedule.next_month }}</span>{% else %}Chưa có{% endif %}</strong></div>
            </div>
            <div class="public-record-heading">Lịch sử thực hiện</div>
            <div class="public-record-table-wrap"><table class="public-record-table"><thead><tr><th>Ngày</th><th>Nội dung</th><th>Chi phí</th></tr></thead><tbody>{% for row in periodic_rows %}<tr><td>{{ row.date }}</td><td>{{ row.content|replace('\n','<br>')|safe }}</td><td>{{ row.cost }}</td></tr>{% else %}<tr><td colspan="3" class="subtle">Chưa có bản ghi.</td></tr>{% endfor %}</tbody></table></div>
          </article>
          <article class="public-data-zone repair">
            <div class="public-data-zone-head">
              <div><h2>Sửa chữa</h2></div>
              <div class="public-data-zone-actions">{% if can_manage_data %}<a class="btn primary" href="{{ url_for('admin_data', tab='maintenance', plate=selected, center=center_filter, focus='repair') }}">Cập nhật</a>{% endif %}<span class="public-data-zone-badge">{{ repair_rows|length }} bản ghi</span></div>
            </div>
            <div class="public-record-heading">Lịch sử sửa chữa</div>
            <div class="public-record-table-wrap"><table class="public-record-table"><thead><tr><th>Ngày</th><th>Nội dung</th><th>Chi phí</th></tr></thead><tbody>{% for row in repair_rows %}<tr><td>{{ row.date }}</td><td>{{ row.content|replace('\n','<br>')|safe }}</td><td>{{ row.cost }}</td></tr>{% else %}<tr><td colspan="3" class="subtle">Chưa có bản ghi.</td></tr>{% endfor %}</tbody></table></div>
          </article>
          <article class="public-data-zone registration">
            <div class="public-data-zone-head">
              <div><h2>Đăng kiểm</h2></div>
              <div class="public-data-zone-actions">{% if can_manage_data %}<a class="btn primary" href="{{ url_for('admin_data', tab='maintenance', plate=selected, center=center_filter, focus='registration') }}">Cập nhật</a>{% endif %}<span class="public-data-zone-badge">{{ registration_rows|length }} bản ghi</span></div>
            </div>
            <div class="public-schedule-values">
              <div class="public-schedule-value"><small>Ngày gần nhất</small><strong>{{ latest_registration.date if latest_registration else 'Chưa cập nhật' }}</strong></div>
              <div class="public-schedule-value"><small>Tháng tiếp theo</small><strong>{{ latest_registration.due_month if latest_registration else 'Chưa cập nhật' }}</strong></div>
              <div class="public-schedule-value full"><small>Trạng thái</small><strong>{{ registry_state.label }}{% if registry_state.days is not none %} · {{ status_text(registry_state) }}{% endif %}</strong></div>
            </div>
            <div class="public-record-heading">Lịch sử đăng kiểm</div>
            <div class="public-record-table-wrap"><table class="public-record-table"><thead><tr><th>Ngày</th><th>Tháng tiếp theo</th><th>Chi phí</th></tr></thead><tbody>{% for row in registration_rows %}<tr><td>{{ row.date }}</td><td>{{ row.due_month }}</td><td>{{ row.cost }}</td></tr>{% else %}<tr><td colspan="3" class="subtle">Chưa có bản ghi.</td></tr>{% endfor %}</tbody></table></div>
          </article>
          <article class="public-data-zone oil">
            <div class="public-data-zone-head">
              <div><h2>Thay dầu</h2></div>
              <div class="public-data-zone-actions">{% if can_manage_data %}<a class="btn primary" href="{{ url_for('admin_data', tab='maintenance', plate=selected, center=center_filter, focus='oil') }}">Cập nhật</a>{% endif %}<span class="public-data-zone-badge">{{ oil_rows|length }} bản ghi</span></div>
            </div>
            <div class="public-record-heading">Lịch sử thay dầu</div>
            <div class="public-record-table-wrap"><table class="public-record-table"><thead><tr><th>Ngày</th><th>Km thay dầu</th><th>Khoảng cách</th><th>Chi phí</th></tr></thead><tbody>{% for row in oil_rows %}<tr><td>{{ row.date }}</td><td>{{ row.odometer }}</td><td>{{ row.distance }}</td><td>{{ row.cost }}</td></tr>{% else %}<tr><td colspan="4" class="subtle">Chưa có bản ghi.</td></tr>{% endfor %}</tbody></table></div>
          </article>
        </section>
        """,
        selected=selected,
        vehicle_options=vehicle_options,
        selected_label=selected_label,
        center_filter=center_filter,
        car=car,
        periodic_rows=periodic_rows,
        repair_rows=repair_rows,
        oil_rows=oil_rows,
        registration_rows=registration_rows,
        schedule=schedule,
        latest_registration=latest_registration,
        registry_state=registry_state,
        total_cost=total_cost,
        format_vnd=format_vnd,
        status_text=status_text,
        can_manage_data=can_manage_data(),
    )
    return page(content, title="Chi tiết xe", active="detail")


@app.route("/detail")
@login_required
def detail():
    data = load_data(["xe", "history", "next", "oil", "registration"])
    if can_manage_data():
        default_center = current_center_scope() if is_area_admin() else CENTER_FILTER_ALL
        center_filter = normalize_center_filter(request.args.get("center") or default_center)
        data["xe"] = center_filtered_vehicle_df(scoped_vehicle_df(data["xe"]), center_filter)
    else:
        center_filter = CENTER_FILTER_ALL
    plates = allowed_plates(data)
    if not plates:
        content = """
        <section class="hero"><div><h1>Tra cứu xe</h1><p class="subtle">Không có biển số nào được cấp quyền.</p></div></section>
        <div class="empty">Chưa có dữ liệu xe để hiển thị.</div>
        """
        return page(content)

    requested_plate = request.args.get("plate", "").strip()
    if not requested_plate and can_manage_data():
        detail_query = request.args.get("q", "").strip()
        detail_rows = dashboard_rows(data, plates)
        if detail_query:
            normalized_query = detail_query.lower()
            detail_rows = [
                row
                for row in detail_rows
                if normalized_query in str(row["plate"]).lower()
                or normalized_query in str(row["car_type"]).lower()
            ]
        detail_page_sizes = [10, 20, 50]
        try:
            detail_per_page = int(request.args.get("detail_per_page", 10))
        except ValueError:
            detail_per_page = 10
        if detail_per_page not in detail_page_sizes:
            detail_per_page = 10
        try:
            detail_page = int(request.args.get("detail_page", 1))
        except ValueError:
            detail_page = 1
        detail_total_pages = max(1, (len(detail_rows) + detail_per_page - 1) // detail_per_page)
        detail_page = min(max(1, detail_page), detail_total_pages)
        detail_start = (detail_page - 1) * detail_per_page
        detail_end = min(detail_start + detail_per_page, len(detail_rows))
        detail_page_rows = detail_rows[detail_start:detail_end]
        detail_pagination_pages = pagination_items(detail_page, detail_total_pages)
        content = render_template_string(
            r"""
            <section class="hero">
              <div>
                <h1>Danh sách xe</h1>
              </div>
              {% if root_admin %}
                <div class="toolbar center-filter-toolbar">
                  {% for center_code, center_label in center_filters.items() %}
                    <a class="btn {{ 'primary active' if center_filter == center_code else '' }}" href="{{ url_for('detail', center=center_code, detail_per_page=detail_per_page, q=detail_query) }}">{{ center_label }}</a>
                  {% endfor %}
                  <span class="badge">{{ detail_list_total }} xe</span>
                </div>
              {% else %}
                <div class="toolbar">
                  <span class="badge">{{ center_filter_label }} · {{ detail_list_total }} xe</span>
                </div>
              {% endif %}
            </section>

            <section class="panel">
              <div class="section-head">
                <div>
                  <h2>Danh sách xe</h2>
                  <p class="subtle" style="margin:3px 0 0">Bấm “Xem chi tiết” ở từng dòng để mở hồ sơ của xe.</p>
                </div>
              </div>
              <div class="dashboard-list-controls">
                <form class="dashboard-list-search" method="get">
                  <input type="hidden" name="center" value="{{ center_filter }}">
                  <input type="hidden" name="detail_per_page" value="{{ detail_per_page }}">
                  <input type="hidden" name="detail_page" value="1">
                  <div class="vehicle-search">
                    <input id="detail-q" name="q" value="{{ detail_query }}" placeholder="Tìm biển số hoặc loại xe">
                  </div>
                  {% if detail_query %}
                    <a class="btn" href="{{ url_for('detail', center=center_filter, detail_per_page=detail_per_page) }}">Xóa lọc</a>
                  {% endif %}
                </form>
                <span class="subtle">
                  Hiển thị {{ detail_start + 1 if detail_list_total else 0 }}-{{ detail_end }} / {{ detail_list_total }} xe
                </span>
                <form class="dashboard-list-size" method="get">
                  <input type="hidden" name="center" value="{{ center_filter }}">
                  <input type="hidden" name="q" value="{{ detail_query }}">
                  <input type="hidden" name="detail_page" value="1">
                  <label for="detail-per-page">Số xe/trang</label>
                  <select class="cost-select" id="detail-per-page" name="detail_per_page">
                    {% for size in detail_page_sizes %}
                      <option value="{{ size }}" {{ "selected" if size == detail_per_page else "" }}>{{ size }}</option>
                    {% endfor %}
                  </select>
                </form>
              </div>
              {% if detail_page_rows %}
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Biển số</th>
                        <th>Loại xe</th>
                        <th>Đăng kiểm</th>
                        <th>Bảo dưỡng</th>
                        <th>Lịch sử</th>
                        <th>Thao tác</th>
                      </tr>
                    </thead>
                    <tbody>
                      {% for row in detail_page_rows %}
                        <tr>
                          <td data-label="Biển số"><a class="code" href="{{ url_for('detail', plate=row.plate, center=center_filter) }}">{{ row.plate }}</a></td>
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
                          <td data-label="Thao tác"><a class="btn primary" href="{{ url_for('detail', plate=row.plate, center=center_filter) }}">Xem chi tiết</a></td>
                        </tr>
                      {% endfor %}
                    </tbody>
                  </table>
                </div>
              {% else %}
                <div class="empty">Không tìm thấy xe phù hợp.</div>
              {% endif %}
              {% if detail_total_pages > 1 %}
                <div class="pagination">
                  <span class="subtle">Trang {{ detail_page }}/{{ detail_total_pages }}</span>
                  <div class="pagination-pages">
                    <a class="btn {{ 'disabled' if detail_page <= 1 else '' }}" href="{{ url_for('detail', center=center_filter, detail_per_page=detail_per_page, detail_page=1, q=detail_query) }}">&laquo; Đầu</a>
                    <a class="btn {{ 'disabled' if detail_page <= 1 else '' }}" href="{{ url_for('detail', center=center_filter, detail_per_page=detail_per_page, detail_page=detail_page - 1, q=detail_query) }}">&lsaquo; Trước</a>
                    {% for p in detail_pagination_pages %}
                      {% if p == 'ellipsis' %}
                        <span class="pagination-ellipsis" aria-hidden="true">&hellip;</span>
                      {% else %}
                        <a class="btn {{ 'active' if p == detail_page else '' }}" href="{{ url_for('detail', center=center_filter, detail_per_page=detail_per_page, detail_page=p, q=detail_query) }}">{{ p }}</a>
                      {% endif %}
                    {% endfor %}
                    <a class="btn {{ 'disabled' if detail_page >= detail_total_pages else '' }}" href="{{ url_for('detail', center=center_filter, detail_per_page=detail_per_page, detail_page=detail_page + 1, q=detail_query) }}">Sau &rsaquo;</a>
                    <a class="btn {{ 'disabled' if detail_page >= detail_total_pages else '' }}" href="{{ url_for('detail', center=center_filter, detail_per_page=detail_per_page, detail_page=detail_total_pages, q=detail_query) }}">Cuối &raquo;</a>
                  </div>
                </div>
              {% endif %}
            </section>
            <script>
              (() => {
                const select = document.getElementById("detail-per-page");
                select?.addEventListener("change", () => select.form?.submit());
              })();
            </script>
            """,
            detail_page_rows=detail_page_rows,
            detail_page_sizes=detail_page_sizes,
            detail_per_page=detail_per_page,
            detail_page=detail_page,
            detail_total_pages=detail_total_pages,
            detail_pagination_pages=detail_pagination_pages,
            detail_start=detail_start,
            detail_end=detail_end,
            detail_list_total=len(detail_rows),
            detail_query=detail_query,
            root_admin=is_admin(),
            center_filter=center_filter,
            center_filter_label=CENTER_FILTER_LABELS.get(center_filter, CENTER_FILTER_LABELS[CENTER_FILTER_ALL]),
            center_filters=CENTER_FILTER_LABELS,
        )
        return page(content, title="Danh sách xe", active="detail")

    selected = request.args.get("plate") or session.get("selected_plate") or plates[0]
    if selected not in plates:
        selected = plates[0]
    session["selected_plate"] = selected

    return render_vehicle_detail_workspace(data, selected, center_filter)

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
    latest_service_date = latest_maintenance_date(
        df_history,
        selected,
        next_item.get("Lịch bảo dưỡng gần nhất", ""),
    )
    repair_count = len([row for row in history_rows if row.get("cost_type") == COST_TYPE_REPAIR])
    periodic_count = len([row for row in history_rows if row.get("cost_type") == COST_TYPE_PERIODIC])
    total_cost = sum(float(row["cost_raw"]) for row in history_rows)
    alerts = due_alerts(next_item) if next_item else []
    maintenance_state = due_state(next_item.get("Dự kiến lần tiếp theo")) if next_item else due_state("")
    registry_state = due_state(next_item.get("Hạn đăng kiểm đến")) if next_item else due_state("")
    plan_due_date = parse_due_date(next_item.get("Dự kiến lần tiếp theo")) if next_item else None
    plan_actionable = bool(plan_due_date and plan_due_date.date() <= now_vn().date())
    plan_type = normalize_plan_type(next_item.get("LoaiKeHoach", PLAN_TYPE_PERIODIC))
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
          (() => {
            const initPlanModals = () => {
              const bindModal = (modalId, openSelector) => {
                const modal = document.getElementById(modalId);
                if (!modal) return;
                const open = () => {
                  modal.hidden = false;
                  requestAnimationFrame(() => modal.classList.add("open"));
                };
                const close = () => {
                  modal.classList.remove("open");
                  modal.hidden = true;
                };
                document.querySelectorAll(openSelector).forEach((button) => button.addEventListener("click", open));
                modal.querySelectorAll("[data-modal-close]").forEach((button) => button.addEventListener("click", close));
                modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
                document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !modal.hidden) close(); });
              };
              bindModal("maintenance-plan-modal", "[data-plan-modal-open]");
              bindModal("maintenance-complete-modal", "[data-plan-complete-open]");
            };
            if (document.readyState === "loading") {
              document.addEventListener("DOMContentLoaded", initPlanModals);
            } else {
              initPlanModals();
            }
          })();
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
              <div>
                <h2>Kế hoạch và đăng kiểm</h2>
                <p class="subtle" style="margin:3px 0 0">Chọn đúng thao tác bên dưới theo tình trạng của xe.</p>
              </div>
            </div>
            <div class="workflow-guide">
              <div class="workflow-action ready">
                <span class="workflow-number">1</span>
                <div class="workflow-copy">
                  <strong>Kế hoạch dự kiến</strong>
                  <span>Nhập ngày và nội dung, chưa nhập tiền.</span>
                </div>
                {% if can_manage_history %}
                  {% if plan_actionable %}
                    <span class="badge">Đã lập</span>
                  {% else %}
                    <button class="btn primary" type="button" data-plan-modal-open>{{ 'Sửa kế hoạch' if next_item.get('Dự kiến lần tiếp theo') else 'Lập kế hoạch' }}</button>
                  {% endif %}
                {% endif %}
              </div>
              <div class="workflow-action {{ 'attention' if plan_actionable else 'muted' }}">
                <span class="workflow-number">2</span>
                <div class="workflow-copy">
                  <strong>Cập nhật thực hiện</strong>
                  <span>{{ 'Đã đến hạn: nhập ngày, nội dung và số tiền.' if plan_actionable else 'Chỉ mở khi kế hoạch đến ngày.' }}</span>
                </div>
                {% if can_manage_history and plan_actionable %}
                  <button class="btn primary" type="button" data-plan-complete-open>Cập nhật</button>
                {% else %}
                  <span class="badge">{{ status_text(maintenance_state) }}</span>
                {% endif %}
              </div>
              <div class="workflow-action ready">
                <span class="workflow-number">3</span>
                <div class="workflow-copy">
                  <strong>Ngoài kế hoạch</strong>
                  <span>Dùng khi phát sinh thực tế không có kế hoạch.</span>
                </div>
                {% if can_manage_history %}
                  <button class="btn" type="button" data-history-add>Thêm bản ghi</button>
                {% endif %}
              </div>
            </div>
            <div class="workflow-cards">
              <article class="workflow-card plan-card">
                <div class="workflow-card-head">
                  <div>
                    <h3>Kế hoạch bảo dưỡng dự kiến</h3>
                    <p>Chỉ là dự kiến cho xe, chưa phải chi phí thực tế.</p>
                  </div>
                  {% if plan_actionable %}
                    <span class="badge workflow-status">{{ status_text(maintenance_state) }}</span>
                  {% elif next_item.get('Dự kiến lần tiếp theo') %}
                    <span class="badge workflow-status">{{ status_text(maintenance_state) }}</span>
                  {% endif %}
                </div>
                {% if next_item.get('Dự kiến lần tiếp theo') %}
                  <div class="workflow-data">
                    <div class="workflow-data-item"><span>Ngày dự kiến</span><strong>{{ format_short_date(next_item.get('Dự kiến lần tiếp theo')) }}</strong></div>
                    <div class="workflow-data-item"><span>Loại</span><strong>{{ plan_type_label }}</strong></div>
                    <div class="workflow-data-item full"><span>Nội dung dự kiến</span><strong>{{ next_item.get('Gợi ý nội dung') or 'Chưa nhập nội dung' }}</strong></div>
                  </div>
                  <div class="workflow-note">Chi phí chỉ nhập ở bước 2 sau khi đến ngày dự kiến.</div>
                {% else %}
                  <div class="workflow-empty">Xe chưa có kế hoạch dự kiến. Nếu muốn lập kế hoạch, bấm “Lập kế hoạch” ở bước 1 để nhập ngày và nội dung.</div>
                  {% if latest_service_date %}<div class="workflow-note">Lần thực hiện gần nhất: <strong>{{ format_short_date(latest_service_date) }}</strong></div>{% endif %}
                {% endif %}
              </article>
              <article class="workflow-card registry-card" id="registry-summary">
                <div class="workflow-card-head">
                  <div>
                    <h3>Đăng kiểm</h3>
                    <p>Theo dõi ngày đăng kiểm và hạn còn hiệu lực.</p>
                  </div>
                  {% if registry_state.key != 'missing' %}<span class="badge workflow-status">{{ status_text(registry_state) }}</span>{% endif %}
                </div>
                <div class="workflow-data">
                  <div class="workflow-data-item"><span>Ngày đăng kiểm</span><strong>{{ format_short_date(next_item.get('Ngày đăng kiểm')) }}</strong></div>
                  <div class="workflow-data-item"><span>Hạn đăng kiểm đến</span><strong>{{ format_short_date(next_item.get('Hạn đăng kiểm đến')) }}</strong></div>
                  <div class="workflow-data-item full"><span>Chi phí đăng kiểm</span><strong>{{ registry_cost }}</strong></div>
                </div>
                {% if can_manage_history %}<div class="workflow-card-actions"><a class="btn" href="{{ url_for('admin_data', tab='maintenance', plate=selected) }}#registration-form">Cập nhật đăng kiểm</a></div>{% endif %}
              </article>
            </div>
            {% if schedule_logs %}
              <details class="workflow-history">
                <summary>Xem lịch sử cập nhật kế hoạch ({{ schedule_logs|length }} lần)</summary>
                <div class="schedule-log-list">
                  {% for log in schedule_logs %}
                    <div class="schedule-log-row">
                      <div><strong>{{ log.saved_at }}</strong><span>{{ format_short_date(log.next_due) }}</span></div>
                      <div><strong>{{ log.plan_type_label }}</strong><span>Kế hoạch dự kiến</span></div>
                      <div class="schedule-log-note">{{ log.suggestion }}</div>
                      {% if can_manage_history %}
                        <div class="schedule-log-actions">
                          <button
                            class="btn"
                            type="button"
                            data-next-log-edit
                            data-record-id="{{ log.id }}"
                            data-last-service="{{ log.last_service_raw }}"
                            data-next-due="{{ log.next_due_raw }}"
                            data-suggestion="{{ log.suggestion_raw }}"
                            data-plan-type="{{ log.plan_type_raw }}"
                            data-registry-date="{{ log.registry_date_raw }}"
                            data-registry-due="{{ log.registry_due_raw }}"
                            data-oil-cost="{{ log.oil_cost_raw }}"
                            data-registry-cost="{{ log.registry_cost_raw }}"
                          >Sửa</button>
                          <form method="post" action="{{ url_for('admin_data') }}" onsubmit="return confirm('Xóa dòng lịch kế hoạch này?')">
                            <input type="hidden" name="action" value="delete_next_log">
                            <input type="hidden" name="plate" value="{{ selected }}">
                            <input type="hidden" name="record_id" value="{{ log.id }}">
                            <input type="hidden" name="return_to" value="lookup">
                            <input type="hidden" name="return_plate" value="{{ selected }}">
                            <button class="btn danger" type="submit">Xóa</button>
                          </form>
                        </div>
                      {% endif %}
                    </div>
                  {% endfor %}
                </div>
              </details>
            {% endif %}
            <div class="legacy-plan-content">
            {% if next_item %}
                <div class="plan-timeline">
                  <div class="plan-step periodic">
                    <span>Lịch bảo dưỡng gần nhất</span>
                    <strong>{{ format_short_date(latest_service_date) }}</strong>
                  </div>
                  <div class="plan-step {{ maintenance_state.key }}">
                    <span>Kế hoạch bảo dưỡng dự kiến</span>
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
                  <strong>{{ next_item.get("Gợi ý nội dung") or "Chưa có kế hoạch" }}</strong>
                </div>
                <div class="plan-costs">
                  <div class="plan-cost oil"><span>Loại kế hoạch</span><strong>{{ plan_type_label }}</strong></div>
                  <div class="plan-cost registry"><span>Chi phí đăng kiểm</span><strong>{{ registry_cost }}</strong></div>
                </div>
              </div>
              {% if schedule_logs %}
                <div class="schedule-log-box">
                  <div class="section-head">
                    <h3>Lịch sử cập nhật kế hoạch</h3>
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
                          <strong>{{ log.plan_type_label }}</strong>
                          <span>Kế hoạch dự kiến</span>
                        </div>
                        <div class="schedule-log-note">{{ log.suggestion }}</div>
                      </div>
                    {% endfor %}
                  </div>
                </div>
              {% endif %}
            {% else %}
              <div class="empty">Chưa có lịch bảo dưỡng tiếp theo.</div>
            {% endif %}
            </div>
          </div>
        </section>

        {% if can_manage_history %}
          <div class="inline-modal-backdrop" id="maintenance-plan-modal" hidden>
            <div class="inline-modal" role="dialog" aria-modal="true" aria-labelledby="maintenance-plan-modal-title">
              <div class="inline-modal-head">
                <h3 id="maintenance-plan-modal-title">Kế hoạch bảo dưỡng dự kiến</h3>
                <button class="dashboard-modal-close" type="button" data-modal-close aria-label="Đóng">×</button>
              </div>
              <div class="inline-modal-body">
                <form method="post" action="{{ url_for('admin_data') }}">
                  <input type="hidden" name="action" value="save_plan">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <input type="hidden" name="return_to" value="lookup">
                  <input type="hidden" name="return_plate" value="{{ selected }}">
                  <div class="inline-modal-grid">
                    <div class="form-row">
                      <div class="field">
                        <label for="plan-modal-date">Ngày dự kiến</label>
                        <input id="plan-modal-date" name="planned_date" type="date" min="{{ tomorrow }}" value="{{ date_input_value(next_item.get('Dự kiến lần tiếp theo', '')) }}" required>
                      </div>
                      <div class="field">
                        <label for="plan-modal-type">Loại kế hoạch</label>
                        <select id="plan-modal-type" name="plan_type">
                          <option value="periodic" {{ 'selected' if plan_type == 'periodic' else '' }}>Bảo dưỡng định kỳ</option>
                          <option value="repair" {{ 'selected' if plan_type == 'repair' else '' }}>Sửa chữa</option>
                        </select>
                      </div>
                    </div>
                    <div class="field">
                      <label for="plan-modal-content">Nội dung dự kiến</label>
                      <textarea id="plan-modal-content" name="content" placeholder="VD: thay dầu, kiểm tra phanh..." required>{{ next_item.get('Gợi ý nội dung', '') if next_item.get('Dự kiến lần tiếp theo') else '' }}</textarea>
                    </div>
                    <div class="subtle">Chưa đến ngày dự kiến, biểu mẫu này không có trường số tiền.</div>
                    <div class="inline-modal-actions">
                      <button class="btn" type="button" data-modal-close>Hủy</button>
                      <button class="btn primary" type="submit">Lưu kế hoạch</button>
                    </div>
                  </div>
                </form>
              </div>
            </div>
          </div>
          <div class="inline-modal-backdrop" id="maintenance-complete-modal" hidden>
            <div class="inline-modal" role="dialog" aria-modal="true" aria-labelledby="maintenance-complete-modal-title">
              <div class="inline-modal-head">
                <h3 id="maintenance-complete-modal-title">Cập nhật thực hiện kế hoạch</h3>
                <button class="dashboard-modal-close" type="button" data-modal-close aria-label="Đóng">×</button>
              </div>
              <div class="inline-modal-body">
                <form method="post" action="{{ url_for('admin_data') }}">
                  <input type="hidden" name="action" value="complete_plan">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <input type="hidden" name="return_to" value="lookup">
                  <input type="hidden" name="return_plate" value="{{ selected }}">
                  <div class="inline-modal-grid">
                    <div class="form-row">
                      <div class="field">
                        <label class="required-field" for="complete-modal-date">Ngày thực hiện <span class="required-mark" aria-hidden="true">*</span></label>
                        <input id="complete-modal-date" class="required-input" name="actual_date" type="date" min="{{ date_input_value(next_item.get('Dự kiến lần tiếp theo', '')) }}" max="{{ today }}" value="{{ today }}" required>
                      </div>
                      <div class="field">
                        <label class="required-field" for="complete-modal-cost">Số tiền thực tế <span class="required-mark" aria-hidden="true">*</span></label>
                        <input id="complete-modal-cost" class="money-input required-input" name="cost" inputmode="numeric" autocomplete="off" placeholder="VD: 1.500.000" required>
                      </div>
                    </div>
                    <div class="required-hint">* Bắt buộc nhập. Ngày thực hiện đang mặc định là hôm nay, có thể thay đổi nếu cần.</div>
                    <div class="field">
                      <label for="complete-modal-type">Loại thực hiện</label>
                      <select id="complete-modal-type" name="cost_type">
                        <option value="{{ PLAN_TYPE_PERIODIC }}" {{ 'selected' if plan_type == PLAN_TYPE_PERIODIC else '' }}>Bảo dưỡng định kỳ</option>
                        <option value="{{ PLAN_TYPE_REPAIR }}" {{ 'selected' if plan_type == PLAN_TYPE_REPAIR else '' }}>Sửa chữa</option>
                      </select>
                    </div>
                    <div class="field">
                      <label for="complete-modal-content">Nội dung thực tế</label>
                      <textarea id="complete-modal-content" name="content" required>{{ next_item.get('Gợi ý nội dung', '') }}</textarea>
                    </div>
                    <div class="inline-modal-actions">
                      <button class="btn" type="button" data-modal-close>Hủy</button>
                      <button class="btn primary" type="submit">Cập nhật đã thực hiện</button>
                    </div>
                  </div>
                </form>
              </div>
            </div>
          </div>
          <div class="inline-modal-backdrop" id="next-log-modal" hidden>
            <div class="inline-modal" role="dialog" aria-modal="true" aria-labelledby="next-log-modal-title">
              <div class="inline-modal-head">
                <h3 id="next-log-modal-title">Sửa dòng lịch kế hoạch</h3>
                <button class="dashboard-modal-close" type="button" data-next-log-close aria-label="Đóng">×</button>
              </div>
              <div class="inline-modal-body">
                <form method="post" action="{{ url_for('admin_data') }}" id="next-log-form">
                  <input type="hidden" name="action" value="update_next_log">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <input type="hidden" name="record_id" id="next-log-record-id">
                  <input type="hidden" name="return_to" value="lookup">
                  <input type="hidden" name="return_plate" value="{{ selected }}">
                  <input type="hidden" name="last_service" id="next-log-last-service">
                  <input type="hidden" name="registry_date" id="next-log-registry-date">
                  <input type="hidden" name="registry_due" id="next-log-registry-due">
                  <input type="hidden" name="oil_cost" id="next-log-oil-cost">
                  <input type="hidden" name="registry_cost" id="next-log-registry-cost">
                  <div class="inline-modal-grid">
                    <div class="form-row">
                      <div class="field">
                        <label for="next-log-date">Ngày dự kiến</label>
                        <input id="next-log-date" name="next_due" type="date" required>
                      </div>
                      <div class="field">
                        <label for="next-log-plan-type">Loại kế hoạch</label>
                        <select id="next-log-plan-type" name="plan_type">
                          <option value="periodic">Bảo dưỡng định kỳ</option>
                          <option value="repair">Sửa chữa</option>
                        </select>
                      </div>
                    </div>
                    <div class="field">
                      <label for="next-log-suggestion">Nội dung dự kiến</label>
                      <textarea id="next-log-suggestion" name="suggestion" required></textarea>
                    </div>
                    <div class="subtle">Đây là dữ liệu lịch sử của kế hoạch. Sửa hoặc xóa dòng này không thay đổi kế hoạch hiện tại của xe.</div>
                    <div class="inline-modal-actions">
                      <button class="btn" type="button" data-next-log-close>Hủy</button>
                      <button class="btn primary" type="submit">Lưu thay đổi</button>
                    </div>
                  </div>
                </form>
              </div>
            </div>
          </div>
          <script>
            (() => {
              const modal = document.getElementById("next-log-modal");
              if (!modal) return;
              const recordId = modal.querySelector("#next-log-record-id");
              const lastService = modal.querySelector("#next-log-last-service");
              const nextDue = modal.querySelector("#next-log-date");
              const planType = modal.querySelector("#next-log-plan-type");
              const suggestion = modal.querySelector("#next-log-suggestion");
              const registryDate = modal.querySelector("#next-log-registry-date");
              const registryDue = modal.querySelector("#next-log-registry-due");
              const oilCost = modal.querySelector("#next-log-oil-cost");
              const registryCost = modal.querySelector("#next-log-registry-cost");
              const open = (button) => {
                recordId.value = button.dataset.recordId || "";
                lastService.value = button.dataset.lastService || "";
                nextDue.value = button.dataset.nextDue || "";
                planType.value = button.dataset.planType || "periodic";
                suggestion.value = button.dataset.suggestion || "";
                registryDate.value = button.dataset.registryDate || "";
                registryDue.value = button.dataset.registryDue || "";
                oilCost.value = button.dataset.oilCost || "";
                registryCost.value = button.dataset.registryCost || "";
                modal.hidden = false;
                requestAnimationFrame(() => modal.classList.add("open"));
              };
              const close = () => {
                modal.classList.remove("open");
                modal.hidden = true;
              };
              document.querySelectorAll("[data-next-log-edit]").forEach((button) => button.addEventListener("click", () => open(button)));
              modal.querySelectorAll("[data-next-log-close]").forEach((button) => button.addEventListener("click", close));
              modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
              document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !modal.hidden) close(); });
            })();
          </script>
        {% endif %}

        <section class="panel" style="margin-top:12px">
          <div class="section-head">
            <div>
              <h2>Lịch sử bảo dưỡng</h2>
                <p class="subtle">Các lần đã thực hiện và đã phát sinh chi phí. Không có kế hoạch vẫn nhập ở đây.</p>
            </div>
            <div class="toolbar">
              {% if can_manage_history %}
                <button class="btn" type="button" data-history-add>Thêm bản ghi</button>
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
                <h3 id="history-record-modal-title" data-history-modal-title>Sửa bản ghi đã thực hiện</h3>
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
                    <div class="subtle">Dùng biểu mẫu này cho công việc đã thực hiện nhưng không có kế hoạch dự kiến.</div>
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
                      <textarea id="history-modal-content" name="content" placeholder="Nhập nội dung bảo dưỡng hoặc sửa chữa" required></textarea>
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
              const addButtons = document.querySelectorAll("[data-history-add]");
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
                if (title) title.textContent = "Thêm bản ghi ngoài kế hoạch";
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
              addButtons.forEach((button) => button.addEventListener("click", openForAdd));
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
        latest_service_date=latest_service_date,
        maintenance_state=maintenance_state,
        plan_actionable=plan_actionable,
        registry_state=registry_state,
        plan_type=plan_type,
        plan_type_label=plan_type_label(plan_type),
        PLAN_TYPE_PERIODIC=PLAN_TYPE_PERIODIC,
        PLAN_TYPE_REPAIR=PLAN_TYPE_REPAIR,
        today=now_vn().strftime("%Y-%m-%d"),
        tomorrow=(now_vn().date() + timedelta(days=1)).isoformat(),
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
        can_manage_history=can_manage_data(),
        date_input_value=date_input_value,
        format_money_input=format_money_input,
    )
    return page(content)


@app.route("/lookup")
def lookup_legacy():
    return redirect(url_for("detail"))


def render_maintenance_workspace(data, selected, center_filter, focus=""):
    history = data.get("history", pd.DataFrame())
    oil = data.get("oil", pd.DataFrame())
    registration = data.get("registration", pd.DataFrame())
    history_view = history[history["Biển số"].astype(str) == selected].copy() if not history.empty else pd.DataFrame()
    history_rows = build_history_rows(history_view)
    periodic_rows = [row for row in history_rows if row["cost_type"] == COST_TYPE_PERIODIC]
    repair_rows = [row for row in history_rows if row["cost_type"] == COST_TYPE_REPAIR]
    oil_rows = build_oil_rows(oil, selected)
    registration_rows = build_registration_rows(registration, selected)
    schedule = maintenance_schedule(history, selected)
    focus = normalize_maintenance_focus(focus)
    focus_label = MAINTENANCE_FOCUS_LABELS.get(focus, "")

    def selected_row(rows, key):
        value = request.args.get(key, "").strip()
        return next((row for row in rows if str(row.get("id", "")) == value), None)

    periodic_edit = selected_row(periodic_rows, "periodic_id")
    repair_edit = selected_row(repair_rows, "repair_id")
    oil_edit = selected_row(oil_rows, "oil_id")
    registration_edit = selected_row(registration_rows, "registration_id")
    vehicle_options = []
    for _, row in data.get("xe", pd.DataFrame()).iterrows():
        plate = normalize_plate(row.get("Biển số"))
        if plate:
            vehicle_options.append({"plate": plate, "label": plate_label_map(data["xe"]).get(plate, plate)})
    vehicle_options.sort(key=lambda item: item["plate"])
    selected_label = next((item["label"] for item in vehicle_options if item["plate"] == selected), selected)
    car_view = data.get("xe", pd.DataFrame())
    car_rows = car_view[car_view["Biển số"].astype(str) == selected] if not car_view.empty else pd.DataFrame()
    car = car_rows.iloc[0].to_dict() if not car_rows.empty else {}
    periodic_form = {"date_raw": "", "content_raw": "", "cost_input": ""}
    repair_form = {"date_raw": "", "content_raw": "", "cost_input": ""}
    oil_form = {"date_raw": "", "odometer_raw": "", "previous_odometer_raw": "", "cost_raw": ""}
    registration_form = {"date_raw": "", "due_month_raw": "", "cost_raw": ""}
    return_to = url_for("admin_data", tab="maintenance", plate=selected, center=center_filter)

    content = render_template_string(
        r"""
        <style>
          .maintenance-workspace { display:grid; gap:18px; }
          .maintenance-toolbar { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
          .maintenance-toolbar select { min-width:280px; }
          .maintenance-toolbar .combo { min-width:360px; }
          .maintenance-toolbar .combo-empty, .public-vehicle-picker .combo-empty { padding:10px 12px; color:#55718e; font-size:13px; }
          .maintenance-summary { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; }
          .maintenance-summary .summary-item { padding:14px 16px; border:1px solid #c8d9eb; border-radius:12px; background:#f8fbfe; }
          .maintenance-summary .summary-item small { display:block; color:#55718e; margin-bottom:5px; }
          .maintenance-summary .summary-item strong { color:#003b70; font-size:20px; }
          .maintenance-record-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:18px; align-items:start; }
          .maintenance-record-grid.focused { grid-template-columns:minmax(0, 1fr); }
          .maintenance-record-grid > .maintenance-card { min-width:0; }
          .maintenance-focus-bar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 0 16px; padding:11px 14px; border:1px solid #b8d3e9; border-radius:9px; background:#eef6fc; color:#164f7d; }
          .maintenance-focus-bar strong { font-size:14px; }
          .maintenance-card { display:flex; flex-direction:column; border:1px solid #b8cde2; border-top:4px solid #006fba; border-radius:12px; background:#fff; padding:18px; min-width:0; }
          .maintenance-card.periodic { border-top-color:#168c58; }
          .maintenance-card.repair { border-top-color:#d23a51; }
          .maintenance-card.oil { border-top-color:#bc7b00; }
          .maintenance-card.registration { border-top-color:#1677b9; }
          .maintenance-card h2 { margin:0; color:#003b70; font-size:20px; }
          .maintenance-card .card-help { margin:4px 0 15px; color:#55718e; font-size:13px; }
          .maintenance-card-kicker { display:flex; justify-content:space-between; align-items:center; gap:10px; margin:0 0 13px; padding:8px 10px; border-radius:9px; background:#f3f7fb; color:#55718e; font-size:12px; }
          .maintenance-card-kicker .card-kicker-label { color:#165888; font-weight:600; }
          .maintenance-card.periodic .maintenance-card-kicker { background:#edf8f2; }
          .maintenance-card.periodic .maintenance-card-kicker .card-kicker-label { color:#147a4b; }
          .maintenance-card.repair .maintenance-card-kicker { background:#fff2f4; }
          .maintenance-card.repair .maintenance-card-kicker .card-kicker-label { color:#b52d49; }
          .maintenance-card.oil .maintenance-card-kicker { background:#fff8e9; }
          .maintenance-card.oil .maintenance-card-kicker .card-kicker-label { color:#9a6400; }
          .maintenance-card.registration .maintenance-card-kicker { background:#eef6fc; }
          .maintenance-card.registration .maintenance-card-kicker .card-kicker-label { color:#176b9f; }
          .maintenance-card form.record-form { display:grid; gap:11px; }
          .maintenance-card .form-row { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }
          .maintenance-card textarea { min-height:76px; resize:vertical; }
          .maintenance-card .form-actions { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
          .maintenance-card .form-actions .btn { margin-top:0; }
          .record-summary { display:flex; gap:10px; flex-wrap:wrap; margin:0 0 14px; }
          .record-summary .summary-chip { border-radius:9px; background:#eaf3fb; padding:9px 12px; color:#164d79; }
          .record-summary .summary-chip strong { display:block; color:#003b70; font-size:17px; }
          .record-summary .summary-chip small { display:block; color:#55718e; }
          .record-upload { margin:13px 0 15px; padding:12px; border:1px dashed #aac3da; border-radius:10px; background:#f8fbfe; }
          .record-upload form { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
          .record-upload input[type=file] { min-width:220px; max-width:100%; }
          .record-table-wrap { flex:0 0 auto; overflow-x:auto; overflow-y:visible; max-height:none; min-height:0; margin-top:14px; border:1px solid #d6e2ee; border-radius:9px; }
          .record-table { width:100%; border-collapse:collapse; min-width:620px; font-size:13px; }
          .record-table th, .record-table td { padding:9px 10px; border-bottom:1px solid #e2ebf3; vertical-align:top; text-align:left; }
          .record-table th { position:sticky; top:0; z-index:1; background:#f1f6fb; color:#315d82; }
          .record-table tr:last-child td { border-bottom:0; }
          .record-table .record-actions { white-space:nowrap; }
          .record-table .record-actions form { display:inline; }
          .record-table .record-actions .btn { padding:6px 9px; font-size:12px; }
          .record-table .record-actions .btn.primary { color:#fff; }
          .record-table .record-actions .btn.danger { color:#ba1837; background:#fff; }
          .registration-edit-notice { display:none; }
          .registration-edit-modal .inline-modal { width:min(620px, 100%); }
          .maintenance-record-edit-modal .inline-modal { width:min(700px, 100%); }
          .maintenance-record-edit-modal .edit-kind-note { margin:0; padding:9px 11px; border-radius:8px; background:#f1f6fb; color:#55718e; font-size:13px; }
          .required-label { color:#ba1837 !important; }
          .required-label::after { content:" *"; }
          .month-display { cursor:help; border-bottom:1px dotted #55718e; }
          @media (max-width: 980px) {
            .maintenance-summary { grid-template-columns:1fr; }
            .maintenance-record-grid { grid-template-columns:1fr; }
          }
          @media (max-width: 620px) {
            .maintenance-card .form-row { grid-template-columns:1fr; }
            .maintenance-toolbar .combo { min-width:0; width:100%; }
          }
        </style>
        <section class="hero">
          <div>
            <h1>Quản lý bảo dưỡng và chi phí</h1>
          </div>
          <div class="maintenance-toolbar">
            <label for="maintenance-plate-search">Chọn xe</label>
            <div class="combo" id="maintenance-plate-combo">
              <input id="maintenance-plate-search" value="{{ selected_label }}" placeholder="Tìm biển số hoặc loại xe" autocomplete="off" aria-controls="maintenance-plate-options">
              <button class="combo-toggle" type="button" aria-label="Mở danh sách xe">▼</button>
              <div class="combo-menu" id="maintenance-plate-options">
                {% for option in vehicle_options %}<button class="combo-option" type="button" data-label="{{ option.label }}" data-url="{{ url_for('admin_data', tab='maintenance', plate=option.plate, center=center_filter, focus=focus) }}">{{ option.label }}</button>{% endfor %}
                <div class="combo-empty" hidden>Không tìm thấy xe phù hợp.</div>
              </div>
            </div>
          </div>
        </section>
        <script>
          (() => {
            const combo = document.getElementById("maintenance-plate-combo");
            const search = document.getElementById("maintenance-plate-search");
            const toggle = combo?.querySelector(".combo-toggle");
            const options = Array.from(document.querySelectorAll("#maintenance-plate-options .combo-option"));
            const empty = combo?.querySelector(".combo-empty");
            if (!combo || !search || !toggle) return;
            const committedLabel = search.value;
            const openMenu = () => combo.classList.add("open");
            const closeMenu = () => combo.classList.remove("open");
            const filterOptions = (query = "") => {
              const normalized = query.trim().toLowerCase();
              let visible = 0;
              options.forEach((option) => {
                const matches = !normalized || option.dataset.label.toLowerCase().includes(normalized);
                option.hidden = !matches;
                if (matches) visible += 1;
              });
              if (empty) empty.hidden = visible > 0;
            };
            const restore = () => {
              search.value = committedLabel;
              filterOptions("");
              closeMenu();
            };
            toggle.addEventListener("click", () => {
              if (combo.classList.contains("open")) restore();
              else { filterOptions(""); openMenu(); search.focus(); }
            });
            search.addEventListener("focus", () => { search.select(); filterOptions(""); openMenu(); });
            search.addEventListener("input", () => { filterOptions(search.value); openMenu(); });
            options.forEach((option) => option.addEventListener("click", () => { window.location.href = option.dataset.url; }));
            search.addEventListener("keydown", (event) => { if (event.key === "Escape") restore(); });
            document.addEventListener("click", (event) => { if (!combo.contains(event.target)) restore(); });
          })();
        </script>
        <section class="panel maintenance-workspace">
          <div class="section-head">
            <div>
              <h2>Xe {{ selected }}</h2>
              <p class="subtle">{{ car.get('Loại xe', 'Chưa cập nhật') }}{% if car.get('TrungTam') %} · {{ center_filter_label }}{% endif %}</p>
            </div>
            <a class="btn" href="{{ url_for('detail', center=center_filter) }}">Về danh sách xe</a>
          </div>
          <div class="maintenance-summary">
            <div class="summary-item"><small>Bảo dưỡng gần nhất</small><strong>{{ schedule.latest_display }}</strong></div>
            <div class="summary-item"><small>Bảo dưỡng tiếp theo</small><strong>{% if schedule.next_date %}<span class="month-display" title="Ngày dự kiến: {{ schedule.next_tooltip }}">{{ schedule.next_month }}</span>{% else %}Chưa có{% endif %}</strong></div>
            <div class="summary-item"><small>Số bản ghi sửa chữa</small><strong>{{ repair_rows|length }}</strong></div>
            <div class="summary-item"><small>Số bản ghi thay dầu</small><strong>{{ oil_rows|length }}</strong></div>
          </div>
        </section>
        {% if focus %}<div class="maintenance-focus-bar"><strong>Đang cập nhật: {{ focus_label }}</strong><a class="btn" href="{{ url_for('admin_data', tab='maintenance', plate=selected, center=center_filter) }}">Hiển thị cả 4 mục</a></div>{% endif %}
        <section class="maintenance-record-grid{{ ' focused' if focus else '' }}">
          {% if not focus or focus == 'periodic' %}
          <article id="maintenance-periodic" class="maintenance-card periodic">
            <div class="maintenance-card-kicker"><span class="card-kicker-label">Lịch tự động theo năm</span><span>{{ periodic_rows|length }} bản ghi</span></div>
            <h2>Bảo dưỡng định kỳ</h2>
            <form class="record-form" method="post" action="{{ url_for('admin_data') }}">
              <input type="hidden" name="action" value="add_periodic">
              <input type="hidden" name="plate" value="{{ selected }}"><input type="hidden" name="center" value="{{ center_filter }}"><input type="hidden" name="focus" value="{{ focus }}"><input type="hidden" name="record_id" value="">
              <div class="form-row">
                <div class="field"><label for="periodic-date">Ngày bảo dưỡng</label><input id="periodic-date" type="date" name="service_date" value="{{ periodic_form.date_raw }}" required></div>
                <div class="field"><label for="periodic-cost">Chi phí</label><input id="periodic-cost" name="cost" value="{{ periodic_form.cost_input }}" placeholder="VD: 1.500.000" inputmode="numeric"></div>
              </div>
              <div class="field"><label for="periodic-content">Nội dung bảo dưỡng</label><textarea id="periodic-content" name="content" required>{{ periodic_form.content_raw }}</textarea></div>
              <div class="form-actions"><button class="btn primary" type="submit">Thêm bảo dưỡng</button></div>
            </form>
            <div class="record-table-wrap"><table class="record-table"><thead><tr><th>Ngày</th><th>Nội dung</th><th>Chi phí</th><th></th></tr></thead><tbody>
              {% for row in periodic_rows %}<tr><td>{{ row.date }}</td><td>{{ row.content|replace('\n', '<br>')|safe }}</td><td>{{ row.cost }}</td><td class="record-actions"><button class="btn" type="button" data-maintenance-edit-open data-edit-kind="periodic" data-record-id="{{ row.id }}" data-record-date="{{ row.date_raw }}" data-record-cost="{{ row.cost_input }}" data-record-content="{{ row.content_raw }}">Sửa</button><form method="post" action="{{ url_for('admin_data') }}" onsubmit="return confirm('Xóa bản ghi bảo dưỡng này?')"><input type="hidden" name="action" value="delete_history"><input type="hidden" name="record_id" value="{{ row.id }}"><input type="hidden" name="plate" value="{{ selected }}"><input type="hidden" name="center" value="{{ center_filter }}"><button class="btn" type="submit">Xóa</button></form></td></tr>{% else %}<tr><td colspan="4" class="subtle">Chưa có lịch sử bảo dưỡng.</td></tr>{% endfor %}
            </tbody></table></div>
          </article>
          {% endif %}
          {% if not focus or focus == 'repair' %}
          <article id="maintenance-repair" class="maintenance-card repair">
            <div class="maintenance-card-kicker"><span class="card-kicker-label">Không tạo lịch</span><span>{{ repair_rows|length }} bản ghi</span></div>
            <h2>Sửa chữa</h2>
            <form class="record-form" method="post" action="{{ url_for('admin_data') }}">
              <input type="hidden" name="action" value="add_repair"><input type="hidden" name="plate" value="{{ selected }}"><input type="hidden" name="center" value="{{ center_filter }}"><input type="hidden" name="focus" value="{{ focus }}"><input type="hidden" name="record_id" value="">
              <div class="form-row"><div class="field"><label for="repair-date">Ngày sửa chữa</label><input id="repair-date" type="date" name="service_date" value="{{ repair_form.date_raw }}" required></div><div class="field"><label for="repair-cost">Số tiền</label><input id="repair-cost" name="cost" value="{{ repair_form.cost_input }}" placeholder="VD: 5.000.000" inputmode="numeric" required></div></div>
              <div class="field"><label for="repair-content">Nội dung sửa chữa</label><textarea id="repair-content" name="content" required>{{ repair_form.content_raw }}</textarea></div>
              <div class="form-actions"><button class="btn primary" type="submit">Thêm sửa chữa</button></div>
            </form>
            <div class="record-upload"><div class="subtle">Nhập nhiều bản ghi</div><form method="post" action="{{ url_for('admin_data') }}" enctype="multipart/form-data"><input type="hidden" name="action" value="import_repairs"><input type="hidden" name="focus" value="{{ focus }}"><input type="hidden" name="center" value="{{ center_filter }}"><input type="hidden" name="plate" value="{{ selected }}"><input type="file" name="file" accept=".xlsx,.xls" required><a class="btn" href="{{ url_for('maintenance_template', kind='repair') }}">Tải mẫu Excel</a><button class="btn primary" type="submit">Nhập Excel</button></form></div>
            <div class="record-table-wrap"><table class="record-table"><thead><tr><th>Ngày</th><th>Nội dung</th><th>Số tiền</th><th></th></tr></thead><tbody>
              {% for row in repair_rows %}<tr><td>{{ row.date }}</td><td>{{ row.content|replace('\n', '<br>')|safe }}</td><td>{{ row.cost }}</td><td class="record-actions"><button class="btn" type="button" data-maintenance-edit-open data-edit-kind="repair" data-record-id="{{ row.id }}" data-record-date="{{ row.date_raw }}" data-record-cost="{{ row.cost_input }}" data-record-content="{{ row.content_raw }}">Sửa</button><form method="post" action="{{ url_for('admin_data') }}" onsubmit="return confirm('Xóa bản ghi sửa chữa này?')"><input type="hidden" name="action" value="delete_history"><input type="hidden" name="record_id" value="{{ row.id }}"><input type="hidden" name="plate" value="{{ selected }}"><input type="hidden" name="center" value="{{ center_filter }}"><button class="btn" type="submit">Xóa</button></form></td></tr>{% else %}<tr><td colspan="4" class="subtle">Chưa có lịch sử sửa chữa.</td></tr>{% endfor %}
            </tbody></table></div>
          </article>
          {% endif %}
          {% if not focus or focus == 'oil' %}
          <article id="maintenance-oil" class="maintenance-card oil">
            <div class="maintenance-card-kicker"><span class="card-kicker-label">Theo dõi quãng đường</span><span>{{ oil_rows|length }} bản ghi</span></div>
            <h2>Thay dầu</h2>
            <form class="record-form" method="post" action="{{ url_for('admin_data') }}">
              <input type="hidden" name="action" value="add_oil"><input type="hidden" name="plate" value="{{ selected }}"><input type="hidden" name="center" value="{{ center_filter }}"><input type="hidden" name="focus" value="{{ focus }}"><input type="hidden" name="record_id" value="">
              <div class="form-row"><div class="field"><label for="oil-date">Ngày thay dầu</label><input id="oil-date" type="date" name="oil_date" value="{{ oil_form.date_raw }}" required></div><div class="field"><label for="oil-cost">Chi phí thay dầu</label><input id="oil-cost" name="cost" value="{{ oil_form.cost_raw }}" placeholder="VD: 800.000" inputmode="numeric" required></div></div>
              <div class="form-row"><div class="field"><label for="oil-odometer">Số km khi thay dầu</label><input id="oil-odometer" name="odometer" value="{{ oil_form.odometer_raw }}" placeholder="VD: 125000" inputmode="numeric" required></div><div class="field"><label for="oil-previous">Số km trước{% if not oil_rows %} *{% endif %}</label><input id="oil-previous" name="previous_odometer" value="{{ oil_form.previous_odometer_raw }}" placeholder="Bắt buộc ở lần đầu" inputmode="numeric"{% if oil_rows %} title="Để trống để tự lấy số km của lần thay dầu gần nhất"{% endif %}></div></div>
              <div class="form-actions"><button class="btn primary" type="submit">Thêm thay dầu</button></div>
            </form>
            <div class="record-upload"><div class="subtle">Nhập nhiều bản ghi</div><form method="post" action="{{ url_for('admin_data') }}" enctype="multipart/form-data"><input type="hidden" name="action" value="import_oil"><input type="hidden" name="focus" value="{{ focus }}"><input type="hidden" name="center" value="{{ center_filter }}"><input type="hidden" name="plate" value="{{ selected }}"><input type="file" name="file" accept=".xlsx,.xls" required><a class="btn" href="{{ url_for('maintenance_template', kind='oil') }}">Tải mẫu Excel</a><button class="btn primary" type="submit">Nhập Excel</button></form></div>
            <div class="record-table-wrap"><table class="record-table"><thead><tr><th>Ngày</th><th>Km thay dầu</th><th>Km trước</th><th>Khoảng cách</th><th>Chi phí</th><th></th></tr></thead><tbody>
              {% for row in oil_rows %}<tr><td>{{ row.date }}</td><td>{{ row.odometer }}</td><td>{{ row.previous_odometer }}</td><td>{{ row.distance }}</td><td>{{ row.cost }}</td><td class="record-actions"><button class="btn" type="button" data-maintenance-edit-open data-edit-kind="oil" data-record-id="{{ row.id }}" data-record-date="{{ row.date_raw }}" data-record-cost="{{ row.cost_raw }}" data-record-odometer="{{ row.odometer_raw }}" data-record-previous-odometer="{{ row.previous_odometer_raw }}">Sửa</button><form method="post" action="{{ url_for('admin_data') }}" onsubmit="return confirm('Xóa bản ghi thay dầu này?')"><input type="hidden" name="action" value="delete_oil"><input type="hidden" name="record_id" value="{{ row.id }}"><input type="hidden" name="plate" value="{{ selected }}"><input type="hidden" name="center" value="{{ center_filter }}"><button class="btn" type="submit">Xóa</button></form></td></tr>{% else %}<tr><td colspan="6" class="subtle">Chưa có lịch sử thay dầu.</td></tr>{% endfor %}
            </tbody></table></div>
          </article>
          {% endif %}
          {% if not focus or focus == 'registration' %}
          <article id="maintenance-registration" class="maintenance-card registration">
            <div class="maintenance-card-kicker"><span class="card-kicker-label">Theo dõi theo tháng</span><span>{{ registration_rows|length }} bản ghi</span></div>
            <h2>Đăng kiểm</h2>
            <form id="registration-form" class="record-form" method="post" action="{{ url_for('admin_data') }}">
              <input type="hidden" name="action" value="add_registration"><input type="hidden" name="plate" value="{{ selected }}"><input type="hidden" name="center" value="{{ center_filter }}"><input type="hidden" name="focus" value="{{ focus }}"><input type="hidden" name="record_id" value="">
              <div class="form-row"><div class="field"><label for="registration-date">Ngày đăng kiểm</label><input id="registration-date" type="date" name="registration_date" value="{{ registration_form.date_raw }}" required></div><div class="field"><label for="registration-month">Tháng dự kiến tiếp theo</label><input id="registration-month" type="month" name="due_month" value="{{ registration_form.due_month_raw }}" required></div></div>
              <div class="field"><label for="registration-cost">Chi phí đăng kiểm</label><input id="registration-cost" name="cost" value="{{ registration_form.cost_raw }}" placeholder="VD: 10.000.000" inputmode="numeric"></div>
              <div class="form-actions"><button class="btn primary" type="submit">Thêm đăng kiểm</button></div>
            </form>
            <div class="record-table-wrap"><table class="record-table"><thead><tr><th>Ngày đăng kiểm</th><th>Dự kiến tiếp theo</th><th>Chi phí</th><th></th></tr></thead><tbody>
              {% for row in registration_rows %}<tr><td>{{ row.date }}</td><td>{{ row.due_month }}</td><td>{{ row.cost }}</td><td class="record-actions"><button class="btn primary" type="button" data-registration-edit-open data-record-id="{{ row.id }}" data-registration-date="{{ row.date_raw }}" data-registration-month="{{ row.due_month_raw }}" data-registration-cost="{{ row.cost_raw }}" aria-label="Sửa bản ghi đăng kiểm ngày {{ row.date }}">Sửa</button><form method="post" action="{{ url_for('admin_data') }}" onsubmit="return confirm('Xóa bản ghi đăng kiểm này?')"><input type="hidden" name="action" value="delete_registration"><input type="hidden" name="record_id" value="{{ row.id }}"><input type="hidden" name="plate" value="{{ selected }}"><input type="hidden" name="center" value="{{ center_filter }}"><button class="btn danger" type="submit" aria-label="Xóa bản ghi đăng kiểm ngày {{ row.date }}">Xóa</button></form></td></tr>{% else %}<tr><td colspan="4" class="subtle">Chưa có lịch sử đăng kiểm.</td></tr>{% endfor %}
            </tbody></table></div>
          </article>
          {% endif %}
          <div class="inline-modal-backdrop registration-edit-modal" id="registration-edit-modal" hidden>
            <section class="inline-modal" role="dialog" aria-modal="true" aria-labelledby="registration-edit-title">
              <div class="inline-modal-head">
                <h3 id="registration-edit-title">Sửa bản ghi đăng kiểm</h3>
                <button class="dashboard-modal-close" type="button" data-registration-edit-close aria-label="Đóng">×</button>
              </div>
              <div class="inline-modal-body">
                <form class="inline-modal-grid" method="post" action="{{ url_for('admin_data') }}" id="registration-edit-form">
                  <input type="hidden" name="action" value="update_registration">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <input type="hidden" name="center" value="{{ center_filter }}">
                  <input type="hidden" name="focus" value="{{ focus }}">
                  <input type="hidden" name="record_id" id="registration-edit-id">
                  <div class="form-row">
                    <div class="field"><label for="registration-edit-date">Ngày đăng kiểm</label><input id="registration-edit-date" type="date" name="registration_date" required></div>
                    <div class="field"><label for="registration-edit-month">Tháng dự kiến tiếp theo</label><input id="registration-edit-month" type="month" name="due_month" required></div>
                  </div>
                  <div class="field"><label for="registration-edit-cost">Chi phí đăng kiểm</label><input id="registration-edit-cost" class="money-input" name="cost" inputmode="numeric" placeholder="VD: 10.000.000"></div>
                  <div class="inline-modal-actions"><button class="btn" type="button" data-registration-edit-close>Hủy</button><button class="btn primary" type="submit">Cập nhật</button></div>
                </form>
              </div>
            </section>
          </div>
          <script>
            (() => {
              const modal = document.getElementById("registration-edit-modal");
              if (!modal) return;
              const idInput = document.getElementById("registration-edit-id");
              const dateInput = document.getElementById("registration-edit-date");
              const monthInput = document.getElementById("registration-edit-month");
              const costInput = document.getElementById("registration-edit-cost");
              const openButtons = document.querySelectorAll("[data-registration-edit-open]");
              const closeButtons = modal.querySelectorAll("[data-registration-edit-close]");
              const open = (button) => {
                idInput.value = button.dataset.recordId || "";
                dateInput.value = button.dataset.registrationDate || "";
                monthInput.value = button.dataset.registrationMonth || "";
                costInput.value = button.dataset.registrationCost || "";
                modal.hidden = false;
                dateInput.focus();
              };
              const close = () => { modal.hidden = true; };
              openButtons.forEach((button) => button.addEventListener("click", () => open(button)));
              closeButtons.forEach((button) => button.addEventListener("click", close));
              modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
              document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !modal.hidden) close(); });
              {% if registration_edit %}
              const initial = document.querySelector("[data-registration-edit-open][data-record-id='{{ registration_edit.id }}']");
              if (initial) open(initial);
              {% endif %}
            })();
          </script>
          <div class="inline-modal-backdrop maintenance-record-edit-modal" id="maintenance-record-edit-modal" hidden>
            <section class="inline-modal" role="dialog" aria-modal="true" aria-labelledby="maintenance-record-edit-title">
              <div class="inline-modal-head">
                <h3 id="maintenance-record-edit-title">Sửa bản ghi</h3>
                <button class="dashboard-modal-close" type="button" data-maintenance-edit-close aria-label="Đóng">×</button>
              </div>
              <div class="inline-modal-body">
                <form class="inline-modal-grid" method="post" action="{{ url_for('admin_data') }}" id="maintenance-record-edit-form">
                  <input type="hidden" name="action" id="maintenance-edit-action">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <input type="hidden" name="center" value="{{ center_filter }}">
                  <input type="hidden" name="focus" value="{{ focus }}">
                  <input type="hidden" name="record_id" id="maintenance-edit-record-id">
                  <p class="edit-kind-note" id="maintenance-edit-note"></p>
                  <div class="form-row">
                    <div class="field"><label for="maintenance-edit-date" id="maintenance-edit-date-label">Ngày thực hiện</label><input id="maintenance-edit-date" type="date" name="service_date" required></div>
                    <div class="field"><label for="maintenance-edit-cost" id="maintenance-edit-cost-label">Chi phí</label><input id="maintenance-edit-cost" name="cost" inputmode="numeric" autocomplete="off" placeholder="VD: 1.500.000"></div>
                  </div>
                  <div class="field" id="maintenance-edit-content-field"><label for="maintenance-edit-content" id="maintenance-edit-content-label">Nội dung</label><textarea id="maintenance-edit-content" name="content"></textarea></div>
                  <div id="maintenance-edit-oil-fields" hidden>
                    <div class="form-row">
                      <div class="field"><label for="maintenance-edit-odometer">Số km khi thay dầu</label><input id="maintenance-edit-odometer" name="odometer" inputmode="numeric" placeholder="VD: 125000"></div>
                      <div class="field"><label for="maintenance-edit-previous-odometer">Số km trước</label><input id="maintenance-edit-previous-odometer" name="previous_odometer" inputmode="numeric" placeholder="Để trống nếu không thay đổi"></div>
                    </div>
                  </div>
                  <div class="inline-modal-actions"><button class="btn" type="button" data-maintenance-edit-close>Hủy</button><button class="btn primary" type="submit">Lưu thay đổi</button></div>
                </form>
              </div>
            </section>
          </div>
          <script>
            (() => {
              const modal = document.getElementById("maintenance-record-edit-modal");
              if (!modal) return;
              const title = document.getElementById("maintenance-record-edit-title");
              const note = document.getElementById("maintenance-edit-note");
              const form = document.getElementById("maintenance-record-edit-form");
              const actionInput = document.getElementById("maintenance-edit-action");
              const recordId = document.getElementById("maintenance-edit-record-id");
              const dateInput = document.getElementById("maintenance-edit-date");
              const dateLabel = document.getElementById("maintenance-edit-date-label");
              const costInput = document.getElementById("maintenance-edit-cost");
              const costLabel = document.getElementById("maintenance-edit-cost-label");
              const contentField = document.getElementById("maintenance-edit-content-field");
              const contentLabel = document.getElementById("maintenance-edit-content-label");
              const contentInput = document.getElementById("maintenance-edit-content");
              const oilFields = document.getElementById("maintenance-edit-oil-fields");
              const odometerInput = document.getElementById("maintenance-edit-odometer");
              const previousOdometerInput = document.getElementById("maintenance-edit-previous-odometer");
              const money = (value) => {
                const digits = String(value || "").replace(/[^\d]/g, "");
                return digits ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".") : "";
              };
              const close = () => {
                modal.classList.remove("open");
                modal.hidden = true;
              };
              const open = (button) => {
                const kind = button.dataset.editKind || "repair";
                const isOil = kind === "oil";
                const isPeriodic = kind === "periodic";
                const labels = isOil
                  ? { title: "Sửa bản ghi thay dầu", note: "Cập nhật ngày, số km và chi phí thay dầu.", date: "Ngày thay dầu", cost: "Chi phí thay dầu", content: "Nội dung thay dầu" }
                  : isPeriodic
                    ? { title: "Sửa bản ghi bảo dưỡng", note: "Cập nhật lần bảo dưỡng đã thực hiện. Lịch tiếp theo sẽ được tính lại theo ngày này.", date: "Ngày bảo dưỡng", cost: "Chi phí", content: "Nội dung bảo dưỡng" }
                    : { title: "Sửa bản ghi sửa chữa", note: "Cập nhật ngày, số tiền và nội dung sửa chữa.", date: "Ngày sửa chữa", cost: "Số tiền", content: "Nội dung sửa chữa" };
                title.textContent = labels.title;
                note.textContent = labels.note;
                actionInput.value = isOil ? "update_oil" : (isPeriodic ? "update_periodic" : "update_repair");
                recordId.value = button.dataset.recordId || "";
                dateInput.name = isOil ? "oil_date" : "service_date";
                dateInput.value = button.dataset.recordDate || "";
                dateLabel.textContent = labels.date;
                costLabel.textContent = labels.cost;
                costInput.value = button.dataset.recordCost || "";
                costInput.required = !isPeriodic;
                contentLabel.textContent = labels.content;
                contentInput.value = button.dataset.recordContent || "";
                contentInput.required = !isOil;
                contentField.hidden = isOil;
                oilFields.hidden = !isOil;
                odometerInput.value = button.dataset.recordOdometer || "";
                previousOdometerInput.value = button.dataset.recordPreviousOdometer || "";
                modal.hidden = false;
                requestAnimationFrame(() => modal.classList.add("open"));
                dateInput.focus();
              };
              document.querySelectorAll("[data-maintenance-edit-open]").forEach((button) => button.addEventListener("click", () => open(button)));
              modal.querySelectorAll("[data-maintenance-edit-close]").forEach((button) => button.addEventListener("click", close));
              modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
              document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !modal.hidden) close(); });
              costInput.addEventListener("input", () => {
                const formatted = money(costInput.value);
                costInput.value = formatted;
                costInput.setSelectionRange(formatted.length, formatted.length);
              });
              form.addEventListener("submit", () => { costInput.value = money(costInput.value); });
            })();
          </script>
        </section>
        """,
        selected=selected,
        center_filter=center_filter,
        center_filter_label=CENTER_FILTER_LABELS.get(center_filter, center_filter),
        vehicle_options=vehicle_options,
        selected_label=selected_label,
        car=car,
        schedule=schedule,
        periodic_rows=periodic_rows,
        repair_rows=repair_rows,
        oil_rows=oil_rows,
        registration_rows=registration_rows,
        periodic_edit=periodic_edit,
        repair_edit=repair_edit,
        oil_edit=oil_edit,
        registration_edit=registration_edit,
        periodic_form=periodic_form,
        repair_form=repair_form,
        oil_form=oil_form,
        registration_form=registration_form,
        focus=focus,
        focus_label=focus_label,
        return_to=return_to,
    )
    return page(content, title="Quản lý bảo dưỡng và chi phí", active="data")


@app.route("/admin/data/template/<kind>")
@login_required
def maintenance_template(kind):
    if not can_manage_data() or kind not in ("repair", "oil"):
        return Response("Forbidden", status=403)
    if kind == "repair":
        columns = ["Biển số", "Ngày sửa chữa", "Nội dung sửa chữa", "Số tiền"]
        filename = "mau-nhap-sua-chua.xlsx"
        title = "HƯỚNG DẪN NHẬP DỮ LIỆU SỬA CHỮA"
        purpose = "Mẫu dùng để nhập nhiều bản ghi sửa chữa cho các xe. Nhập dữ liệu tại sheet Dữ liệu."
        instructions = [
            "Mỗi dòng trên sheet Dữ liệu là một lần sửa chữa của một xe.",
            "Không đổi tên hoặc thứ tự các cột; biển số phải trùng với biển số đang có trong hệ thống.",
            "Ngày sửa chữa nhập theo dạng DD/MM/YYYY hoặc YYYY-MM-DD, ví dụ 19/05/2026.",
            "Số tiền chỉ nhập số, không nhập chữ VND hoặc ký hiệu tiền tệ; ví dụ 2376000.",
            "Có thể xóa dòng ví dụ trong phần Dữ liệu trước khi tải file lên hệ thống.",
        ]
        sample = ["29B-078.52", "19/05/2026", "Thay ắc quy; kiểm tra cảm biến", "2376000"]
    else:
        columns = ["Biển số", "Ngày thay dầu", "Số km khi thay dầu", "Số km trước", "Chi phí thay dầu"]
        filename = "mau-nhap-thay-dau.xlsx"
        title = "HƯỚNG DẪN NHẬP DỮ LIỆU THAY DẦU"
        purpose = "Mẫu dùng để nhập nhiều lần thay dầu cho các xe. Nhập dữ liệu tại sheet Dữ liệu."
        instructions = [
            "Mỗi dòng trên sheet Dữ liệu là một lần thay dầu của một xe.",
            "Không đổi tên hoặc thứ tự các cột; biển số phải trùng với biển số đang có trong hệ thống.",
            "Ngày thay dầu nhập theo dạng DD/MM/YYYY hoặc YYYY-MM-DD, ví dụ 01/09/2026.",
            "Số km khi thay dầu và Số km trước nhập số nguyên dạng chữ, không nhập dấu chấm hoặc dấu phẩy.",
            "Với lần thay dầu đầu tiên của xe, bắt buộc nhập Số km trước; các lần sau có thể để trống để hệ thống tự lấy lần trước.",
            "Chi phí thay dầu chỉ nhập số, không nhập chữ VND hoặc ký hiệu tiền tệ; ví dụ 800000.",
            "Có thể xóa dòng ví dụ trong phần Dữ liệu trước khi tải file lên hệ thống.",
        ]
        sample = ["29B-078.52", "01/09/2026", "100000", "95000", "800000"]
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book
        worksheet = workbook.add_worksheet("Dữ liệu")
        guide = workbook.add_worksheet("Hướng dẫn")

        text_format = workbook.add_format({"num_format": "@", "valign": "top"})
        header = workbook.add_format({"bold": True, "bg_color": "#eaf3fb", "font_color": "#003b70", "border": 1, "border_color": "#c8dced", "align": "center", "valign": "vcenter"})
        guide_title = workbook.add_format({"bold": True, "font_size": 14, "font_color": "#003b70", "bg_color": "#eaf3fb", "align": "center", "valign": "vcenter"})
        guide_subtitle = workbook.add_format({"font_color": "#496b88", "text_wrap": True, "valign": "top"})
        guide_section = workbook.add_format({"bold": True, "font_color": "#003b70", "bg_color": "#f3f7fb", "border": 1, "border_color": "#c8dced"})
        guide_text = workbook.add_format({"font_color": "#1f3b56", "text_wrap": True, "valign": "top"})
        sample_format = workbook.add_format({"num_format": "@", "bg_color": "#fff9e8", "border": 1, "border_color": "#ead7a0", "text_wrap": True, "valign": "top"})

        for column_index, column_name in enumerate(columns):
            worksheet.write_string(0, column_index, column_name, header)
            worksheet.write_blank(1, column_index, None, text_format)
            worksheet.set_column(column_index, column_index, 24, text_format)
        worksheet.freeze_panes(1, 0)
        worksheet.set_row(0, 26, header)
        worksheet.autofilter(0, 0, 0, len(columns) - 1)

        last_column = len(columns) - 1
        guide.merge_range(0, 0, 0, last_column, title, guide_title)
        guide.merge_range(1, 0, 1, last_column, purpose, guide_subtitle)
        guide.merge_range(3, 0, 3, last_column, "Hướng dẫn nhập", guide_section)
        for offset, instruction in enumerate(instructions, start=4):
            guide.merge_range(offset, 0, offset, last_column, f"{offset - 3}. {instruction}", guide_text)
            guide.set_row(offset, 30 if len(instruction) > 100 else 22)

        sample_section_row = 4 + len(instructions) + 1
        sample_header_row = sample_section_row + 1
        sample_row = sample_header_row + 1
        guide.merge_range(sample_section_row, 0, sample_section_row, last_column, "Dữ liệu mẫu", guide_section)
        for column_index, column_name in enumerate(columns):
            guide.write_string(sample_header_row, column_index, column_name, header)
            guide.write_string(sample_row, column_index, str(sample[column_index]), sample_format)
        guide.set_row(sample_header_row, 26, header)
        guide.set_row(sample_row, 32, sample_format)
        guide.autofilter(sample_header_row, 0, sample_header_row, last_column)
        guide.freeze_panes(sample_header_row + 1, 0)
        guide.set_column(0, last_column, 24, text_format)
        guide.set_column(0, 0, 20, text_format)
        if kind == "repair":
            guide.set_column(2, 2, 42, text_format)
        else:
            guide.set_column(1, 1, 20, text_format)
            guide.set_column(2, 3, 20, text_format)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/admin/data", methods=["GET", "POST"])
@login_required
def admin_data():
    if not can_manage_data():
        return Response("Forbidden", status=403)

    requested_center = request.form.get("center") if request.method == "POST" else request.args.get("center")
    center_filter = normalize_center_filter(requested_center)
    requested_focus = request.form.get("focus") if request.method == "POST" else request.args.get("focus")
    focus = normalize_maintenance_focus(requested_focus)
    session["admin_center_filter"] = center_filter
    scope_data = load_data(["xe"])
    all_scope_plates = set(safe_unique(scope_data["xe"], "Biển số"))
    managed_plates = scoped_vehicle_plates(scope_data, include_inactive=True)
    import_scope_plates = set(safe_unique(center_filtered_vehicle_df(scoped_vehicle_df(scope_data["xe"]), center_filter), "Biển số"))

    if request.method == "POST":
        action = request.form.get("action")
        plate = normalize_plate(request.form.get("plate", ""))
        return_to = request.form.get("return_to", "").strip()
        return_plate = normalize_plate(request.form.get("return_plate", ""))
        redirect_tab = "vehicles"
        if action == "save_vehicle":
            allowed_write = plate in managed_plates or (
                (is_admin() or is_center_admin()) and plate not in all_scope_plates
            )
        else:
            allowed_write = plate in managed_plates
        if not allowed_write and action not in (None, ""):
            flash("Bạn không có quyền quản lý xe này.", "error")
            return redirect(url_for("admin_data", tab="vehicles", center=center_filter))
        if action == "save_vehicle":
            redirect_tab = "vehicles"
            vehicle_center = current_center_scope()
            if not is_center_admin() and request.form.get("form_mode") != "edit":
                vehicle_center = center_filter if center_filter != CENTER_FILTER_ALL else MAIN_CENTER_CODE
            manufacture_year = normalize_manufacture_year(request.form.get("manufacture_year", ""))
            if request.form.get("manufacture_year", "").strip() and not manufacture_year:
                flash("Năm sản xuất phải là năm hợp lệ gồm 4 chữ số.", "error")
                return redirect(url_for("admin_data", tab="vehicles", mode=request.form.get("form_mode", ""), plate=plate, center=center_filter))
            parking_location = (
                current_area_label()
                if is_area_admin()
                else request.form.get("parking_location", "") or default_parking_location(vehicle_center)
            )
            if upsert_vehicle(
                plate,
                request.form.get("car_type", ""),
                manufacture_year,
                request.form.get("status", ""),
                parking_location,
                vehicle_center,
            ):
                flash(f"Đã lưu xe {plate}.", "success")
                if request.form.get("form_mode") != "edit":
                    plate = ""
            else:
                flash("Vui lòng nhập biển số xe.", "error")
        elif action == "add_periodic":
            redirect_tab = "maintenance"
            ok = add_history_record(plate, request.form.get("service_date", ""), request.form.get("content", ""), request.form.get("cost", ""), COST_TYPE_PERIODIC)
            flash("Đã thêm bản ghi bảo dưỡng định kỳ." if ok else "Vui lòng nhập đủ ngày và nội dung bảo dưỡng.", "success" if ok else "error")
        elif action == "update_periodic":
            redirect_tab = "maintenance"
            ok = update_history_record(request.form.get("record_id", ""), plate, request.form.get("service_date", ""), request.form.get("content", ""), request.form.get("cost", ""), COST_TYPE_PERIODIC)
            flash("Đã cập nhật bản ghi bảo dưỡng định kỳ." if ok else "Không tìm thấy bản ghi bảo dưỡng cần sửa.", "success" if ok else "error")
        elif action == "add_repair":
            redirect_tab = "maintenance"
            ok = add_history_record(plate, request.form.get("service_date", ""), request.form.get("content", ""), request.form.get("cost", ""), COST_TYPE_REPAIR)
            flash("Đã thêm bản ghi sửa chữa." if ok else "Vui lòng nhập đủ ngày và nội dung sửa chữa.", "success" if ok else "error")
        elif action == "update_repair":
            redirect_tab = "maintenance"
            ok = update_history_record(request.form.get("record_id", ""), plate, request.form.get("service_date", ""), request.form.get("content", ""), request.form.get("cost", ""), COST_TYPE_REPAIR)
            flash("Đã cập nhật bản ghi sửa chữa." if ok else "Không tìm thấy bản ghi sửa chữa cần sửa.", "success" if ok else "error")
        elif action in ("add_oil", "update_oil"):
            redirect_tab = "maintenance"
            ok, message = save_oil_record(plate, request.form.get("oil_date", ""), request.form.get("odometer", ""), request.form.get("previous_odometer", ""), request.form.get("cost", ""), request.form.get("record_id", "") if action == "update_oil" else "")
            flash(message, "success" if ok else "error")
        elif action == "delete_oil":
            redirect_tab = "maintenance"
            ok = delete_oil_record(request.form.get("record_id", ""), plate)
            flash("Đã xóa bản ghi thay dầu." if ok else "Không tìm thấy bản ghi thay dầu cần xóa.", "success" if ok else "error")
        elif action in ("add_registration", "update_registration"):
            redirect_tab = "maintenance"
            ok, message = save_registration_record(plate, request.form.get("registration_date", ""), request.form.get("due_month", ""), request.form.get("cost", ""), request.form.get("record_id", "") if action == "update_registration" else "")
            flash(message, "success" if ok else "error")
        elif action == "delete_registration":
            redirect_tab = "maintenance"
            ok = delete_registration_record(request.form.get("record_id", ""), plate)
            flash("Đã xóa bản ghi đăng kiểm." if ok else "Không tìm thấy bản ghi đăng kiểm cần xóa.", "success" if ok else "error")
        elif action == "import_repairs":
            redirect_tab = "maintenance"
            ok, message = import_repair_records(request.files.get("file"), import_scope_plates)
            flash(message, "success" if ok else "error")
        elif action == "import_oil":
            redirect_tab = "maintenance"
            ok, message = import_oil_records(request.files.get("file"), import_scope_plates)
            flash(message, "success" if ok else "error")
        elif action == "save_plan":
            redirect_tab = "maintenance"
            ok, message = save_maintenance_plan(
                plate,
                request.form.get("planned_date", request.form.get("next_due", "")),
                request.form.get("content", request.form.get("suggestion", "")),
                request.form.get("plan_type", PLAN_TYPE_PERIODIC),
            )
            flash(message, "success" if ok else "error")
        elif action == "complete_plan":
            redirect_tab = "maintenance"
            ok, message = complete_maintenance_plan(
                plate,
                request.form.get("actual_date", request.form.get("service_date", "")),
                request.form.get("content", ""),
                request.form.get("cost", ""),
                request.form.get("cost_type", PLAN_TYPE_PERIODIC),
            )
            flash(message, "success" if ok else "error")
        elif action == "delete_plan":
            redirect_tab = "maintenance"
            if delete_maintenance_plan(plate):
                flash(f"Đã xóa kế hoạch dự kiến của xe {plate}.", "success")
            else:
                flash("Không tìm thấy kế hoạch cần xóa.", "error")
        elif action == "save_registration":
            redirect_tab = "maintenance"
            if save_registration(
                plate,
                request.form.get("registry_date", ""),
                request.form.get("registry_due", ""),
                request.form.get("registry_cost", ""),
                request.form.get("registry_odo", ""),
            ):
                flash(f"Đã cập nhật thông tin đăng kiểm cho xe {plate}.", "success")
            else:
                flash("Vui lòng chọn xe trước khi cập nhật đăng kiểm.", "error")
        elif action == "save_next":
            redirect_tab = "maintenance"
            ok, message = save_maintenance_plan(
                plate,
                request.form.get("next_due", ""),
                request.form.get("suggestion", ""),
                request.form.get("plan_type", PLAN_TYPE_PERIODIC),
            )
            if request.form.get("registry_date") or request.form.get("registry_due") or request.form.get("registry_cost"):
                save_registration(
                    plate,
                    request.form.get("registry_date", ""),
                    request.form.get("registry_due", ""),
                    request.form.get("registry_cost", ""),
                    request.form.get("registry_odo", ""),
                )
            flash(message, "success" if ok else "error")
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
                request.form.get("plan_type", PLAN_TYPE_PERIODIC),
            ):
                flash("Đã cập nhật dòng lịch kế hoạch.", "success")
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
            if record_id and delete_history_record(record_id, plate):
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
        if return_to == "alerts":
            return redirect(url_for("maintenance_alerts"))
        if return_to == "lookup":
            target_plate = return_plate or plate
            return redirect(url_for("detail", plate=target_plate) if target_plate else url_for("detail"))
        if redirect_tab == "vehicles":
            return redirect(
                url_for("admin_data", tab=redirect_tab, page=page_arg, center=center_filter)
                if page_arg
                else url_for("admin_data", tab=redirect_tab, center=center_filter)
            )
        maintenance_redirect_args = {"tab": redirect_tab, "plate": plate, "center": center_filter}
        if focus:
            maintenance_redirect_args["focus"] = focus
        return redirect(
            url_for("admin_data", **maintenance_redirect_args)
            if plate
            else url_for("admin_data", tab=redirect_tab, center=center_filter, **({"focus": focus} if focus else {}))
        )

    data = load_data(["xe", "history", "next", "oil", "registration"])
    data["xe"] = scoped_vehicle_df(data["xe"])
    data["xe"] = center_filtered_vehicle_df(data["xe"], center_filter)
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
    latest_service_date = ""
    maintenance_state = due_state("")
    plan_actionable = False
    plan_type = PLAN_TYPE_PERIODIC
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
        latest_service_date = latest_maintenance_date(
            data["history"],
            selected,
            next_item.get("Lịch bảo dưỡng gần nhất", ""),
        )
        maintenance_state = due_state(next_item.get("Dự kiến lần tiếp theo", ""))
        plan_due_date = parse_due_date(next_item.get("Dự kiến lần tiếp theo", "")) if next_item else None
        plan_actionable = bool(plan_due_date and plan_due_date.date() <= now_vn().date())
        plan_type = normalize_plan_type(next_item.get("LoaiKeHoach", PLAN_TYPE_PERIODIC))
        if new_history_mode:
            selected_history = {}
        elif history_edit_id:
            edit_row = history_view[history_view["ID"].astype(str) == history_edit_id]
            if not edit_row.empty:
                selected_history = edit_row.iloc[0].to_dict()
    if tab == "maintenance" and selected:
        return render_maintenance_workspace(data, selected, center_filter, focus)

    car_status = normalize_vehicle_status(car.get("Trạng thái", ""))
    area_admin = is_area_admin()
    parking_location = (
        current_area_label()
        if area_admin
        else str(car.get(CAR_LOCATION_COLUMN, "") or "").strip() or default_parking_location(car.get(CAR_CENTER_COLUMN))
    )
    parking_location_options = [parking_location] if area_admin else list(PARKING_LOCATION_OPTIONS)
    if parking_location and parking_location not in parking_location_options:
        parking_location_options.insert(0, parking_location)

    vehicle_rows = []
    for _, row in data["xe"].iterrows():
        plate_value = str(row.get("Biển số", "")).strip()
        if not plate_value:
            continue
        next_row = data["next"][data["next"]["Biển số"].astype(str) == plate_value]
        next_due = next_row.iloc[0].get("Dự kiến lần tiếp theo", "") if not next_row.empty else ""
        parking_center = row.get(CAR_CENTER_COLUMN, MAIN_CENTER_CODE)
        parking_location_value = str(row.get(CAR_LOCATION_COLUMN, "") or "").strip() or default_parking_location(parking_center)
        vehicle_rows.append(
            {
                "plate": plate_value,
                "car_type": row.get("Loại xe", ""),
                "location": parking_location_value,
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
                str(row.get(key, "")) for key in ("plate", "car_type", "location", "year", "status", "next_due")
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
    vehicle_pagination_pages = pagination_items(vehicle_page, vehicle_total_pages)
    vehicle_start = (vehicle_page - 1) * vehicle_per_page
    vehicle_end = min(vehicle_start + vehicle_per_page, vehicle_total)
    vehicle_page_rows = vehicle_rows[vehicle_start:vehicle_end]

    content = render_template_string(
        r"""
        <section class="hero">
          <div>
            <h1>Quản lý dữ liệu xe</h1>
          </div>
          {% if root_admin %}
            <div class="toolbar center-filter-toolbar">
              {% for center_code, center_label in center_filters.items() %}
                <a class="btn {{ 'primary' if center_filter == center_code else '' }}" href="{{ url_for('admin_data', center=center_code, tab=tab) }}">{{ center_label }}</a>
              {% endfor %}
            </div>
          {% else %}
            <span class="badge">{{ center_filter_label }}</span>
          {% endif %}
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
              {% if root_admin %}
                <a href="{{ url_for('admin', view='accounts') }}">
                  Tài khoản khu vực
                </a>
              {% endif %}
              {% if root_admin or center_admin %}
                <a href="{{ url_for('admin', view='access') }}">
                  Quản lý mã truy cập
                </a>
              {% endif %}
              {% if root_admin %}
                <a href="{{ url_for('admin', view='audit') }}">
                  Lịch sử truy cập
                </a>
              {% endif %}
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
              <input type="hidden" name="center" value="{{ center_filter }}">
              <div class="field">
                <label for="plate">Biển số</label>
                <input id="plate" name="plate" value="{{ car.get("Biển số", selected) }}" placeholder="VD: 30A12345" required>
              </div>
              <div class="field">
                <label for="car-type">Loại xe</label>
                <input id="car-type" name="car_type" value="{{ car.get("Loại xe", "") }}" placeholder="VD: Toyota Camry">
              </div>
              {% if not area_admin %}
                <div class="field">
                  <label for="parking-location">Địa điểm xe đỗ</label>
                  <select id="parking-location" name="parking_location">
                    {% for location in parking_location_options %}
                      <option value="{{ location }}" {{ 'selected' if parking_location == location else '' }}>{{ location }}</option>
                    {% endfor %}
                  </select>
                </div>
              {% endif %}
              <div class="form-row">
                <div class="field">
                  <label for="manufacture-year">Năm sản xuất</label>
                  <input id="manufacture-year" name="manufacture_year" type="number" min="1900" max="{{ now_vn().year + 1 }}" inputmode="numeric" placeholder="VD: 2020" value="{{ normalize_manufacture_year(car.get("Năm sản xuất", "")) }}">
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
                <input type="hidden" name="center" value="{{ center_filter }}">
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
                      <th>Địa điểm đỗ</th>
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
                        <td data-label="Địa điểm đỗ">{{ row.location or "Chưa cập nhật" }}</td>
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
                    <a data-preserve-scroll class="btn {{ 'disabled' if vehicle_page <= 1 else '' }}" href="{{ url_for('admin_data', tab='vehicles', mode=vehicle_mode, plate=selected, page=1, q=vehicle_query) if vehicle_mode == 'edit' else url_for('admin_data', tab='vehicles', mode=vehicle_mode, page=1, q=vehicle_query) }}">&laquo; Đầu</a>
                    <a data-preserve-scroll class="btn {{ 'disabled' if vehicle_page <= 1 else '' }}" href="{{ url_for('admin_data', tab='vehicles', mode=vehicle_mode, plate=selected, page=vehicle_page - 1, q=vehicle_query) if vehicle_mode == 'edit' else url_for('admin_data', tab='vehicles', mode=vehicle_mode, page=vehicle_page - 1, q=vehicle_query) }}">Trước</a>
                    {% for p in vehicle_pagination_pages %}
                      {% if p == 'ellipsis' %}
                        <span class="pagination-ellipsis" aria-hidden="true">&hellip;</span>
                      {% else %}
                        <a data-preserve-scroll class="btn {{ 'active' if p == vehicle_page else '' }}" href="{{ url_for('admin_data', tab='vehicles', mode=vehicle_mode, plate=selected, page=p, q=vehicle_query) if vehicle_mode == 'edit' else url_for('admin_data', tab='vehicles', mode=vehicle_mode, page=p, q=vehicle_query) }}">{{ p }}</a>
                      {% endif %}
                    {% endfor %}
                    <a data-preserve-scroll class="btn {{ 'disabled' if vehicle_page >= vehicle_total_pages else '' }}" href="{{ url_for('admin_data', tab='vehicles', mode=vehicle_mode, plate=selected, page=vehicle_page + 1, q=vehicle_query) if vehicle_mode == 'edit' else url_for('admin_data', tab='vehicles', mode=vehicle_mode, page=vehicle_page + 1, q=vehicle_query) }}">Sau</a>
                    <a data-preserve-scroll class="btn {{ 'disabled' if vehicle_page >= vehicle_total_pages else '' }}" href="{{ url_for('admin_data', tab='vehicles', mode=vehicle_mode, plate=selected, page=vehicle_total_pages, q=vehicle_query) if vehicle_mode == 'edit' else url_for('admin_data', tab='vehicles', mode=vehicle_mode, page=vehicle_total_pages, q=vehicle_query) }}">Cuối &raquo;</a>
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
              <div class="toolbar">
                {% if selected %}<a class="btn" href="{{ url_for('detail', plate=selected) }}" data-maintenance-exit>Quay lại chi tiết xe</a>{% endif %}
                <span class="badge">{{ selected or "Chưa chọn" }}</span>
              </div>
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
                <h2>1. Kế hoạch bảo dưỡng dự kiến</h2>
                <span class="badge">{{ selected }}</span>
              </div>
              <div class="subtle" style="margin-bottom:10px">Kế hoạch chỉ nhập ngày dự kiến và nội dung. Chưa đến hạn sẽ chưa nhập số tiền.</div>
              {% if plan_actionable %}
                <div class="alert warning">Kế hoạch đã đến hạn. Không sửa kế hoạch ở đây; hãy dùng mục <strong>Cập nhật thực hiện</strong> bên dưới để nhập kết quả và số tiền.</div>
              {% else %}
              <form method="post" class="maintenance-form">
                <input type="hidden" name="action" value="save_plan">
                <input type="hidden" name="plate" value="{{ selected }}">
                <div class="form-row">
                  <div class="field">
                    <label for="plan-date">Ngày dự kiến</label>
                    <input id="plan-date" name="planned_date" type="date" min="{{ tomorrow }}" value="{{ date_input_value(next_item.get("Dự kiến lần tiếp theo", "")) }}" required>
                  </div>
                  <div class="field">
                    <label for="plan-type">Loại kế hoạch</label>
                    <select id="plan-type" name="plan_type">
                      <option value="periodic" {{ 'selected' if plan_type == 'periodic' else '' }}>Bảo dưỡng định kỳ</option>
                      <option value="repair" {{ 'selected' if plan_type == 'repair' else '' }}>Sửa chữa</option>
                    </select>
                  </div>
                </div>
                <div class="field">
                  <label for="plan-content">Nội dung dự kiến</label>
                  <textarea id="plan-content" name="content" placeholder="VD: thay dầu, kiểm tra phanh..." required>{{ next_item.get("Gợi ý nội dung", "") if next_item.get("Dự kiến lần tiếp theo") else "" }}</textarea>
                </div>
                <button class="btn primary full" type="submit">{{ 'Cập nhật kế hoạch' if next_item.get("Dự kiến lần tiếp theo") else 'Thêm kế hoạch' }}</button>
              </form>
              {% endif %}
              {% if not plan_actionable %}
                <div class="workflow-inline-step">
                  <h3>2. Cập nhật thực hiện</h3>
                  {% if next_item.get("Dự kiến lần tiếp theo") %}
                    <div class="workflow-empty">Chưa đến ngày kế hoạch. Khi đến hạn, biểu mẫu nhập ngày thực tế, nội dung và số tiền sẽ xuất hiện ở đây.</div>
                  {% else %}
                    <div class="workflow-empty">Chưa có kế hoạch dự kiến nên chưa có bước thực hiện.</div>
                  {% endif %}
                </div>
              {% endif %}
              {% if plan_actionable %}
                <form method="post" class="maintenance-form">
                  <input type="hidden" name="action" value="complete_plan">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <div class="alert warning"><strong>2. Cập nhật thực hiện:</strong> nhập ngày thực tế, nội dung và số tiền để chuyển thành lịch sử.</div>
                  <div class="form-row">
                    <div class="field">
                      <label class="required-field" for="actual-date">Ngày thực hiện <span class="required-mark" aria-hidden="true">*</span></label>
                      <input id="actual-date" class="required-input" name="actual_date" type="date" min="{{ date_input_value(next_item.get("Dự kiến lần tiếp theo", "")) }}" max="{{ today }}" value="{{ today }}" required>
                    </div>
                    <div class="field">
                      <label class="required-field" for="actual-cost">Số tiền thực tế <span class="required-mark" aria-hidden="true">*</span></label>
                      <input id="actual-cost" class="money-input required-input" name="cost" inputmode="numeric" autocomplete="off" placeholder="VD: 1.500.000" required>
                    </div>
                  </div>
                  <div class="field">
                    <label for="actual-content">Nội dung thực tế</label>
                    <textarea id="actual-content" name="content" required>{{ next_item.get("Gợi ý nội dung", "") }}</textarea>
                  </div>
                  <input type="hidden" name="cost_type" value="{{ plan_type }}">
                  <button class="btn primary full" type="submit">Cập nhật đã thực hiện</button>
                </form>
              {% endif %}
              {% if next_item.get("Dự kiến lần tiếp theo") %}
                <form method="post" class="maintenance-form" onsubmit="return confirm('Xóa kế hoạch dự kiến của xe này?')">
                  <input type="hidden" name="action" value="delete_plan">
                  <input type="hidden" name="plate" value="{{ selected }}">
                  <button class="btn danger full" type="submit">Xóa kế hoạch dự kiến</button>
                </form>
              {% endif %}
              <form method="post" class="maintenance-form" id="registration-form">
                <input type="hidden" name="action" value="save_registration">
                <input type="hidden" name="plate" value="{{ selected }}">
                <div class="section-head"><h3>Thông tin đăng kiểm</h3></div>
                <div class="form-row">
                  <div class="field">
                    <label for="registry-date">Ngày đăng kiểm</label>
                    <input id="registry-date" name="registry_date" type="date" value="{{ date_input_value(next_item.get("Ngày đăng kiểm", "")) }}">
                  </div>
                  <div class="field">
                    <label for="registry-due">Hạn đăng kiểm đến</label>
                    <input id="registry-due" name="registry_due" type="date" value="{{ date_input_value(next_item.get("Hạn đăng kiểm đến", "")) }}">
                  </div>
                </div>
                <div class="field">
                  <label for="registry-cost">Chi phí đăng kiểm thực tế</label>
                  <input id="registry-cost" class="money-input" name="registry_cost" value="{{ format_money_input(next_item.get("Chi phí đăng kiểm", "")) if next_item.get("Chi phí đăng kiểm") else "" }}" inputmode="numeric" autocomplete="off" placeholder="VD: 2.500.000">
                </div>
                <button class="btn full" type="submit">Lưu thông tin đăng kiểm</button>
              </form>
            </section>

            <section class="panel maintenance-card">
              <div class="section-head">
                <div>
                  <h2>{{ "Sửa bản ghi đã thực hiện" if selected_history else "3. Ngoài kế hoạch" }}</h2>
                  {% if not selected_history %}<p class="subtle" style="margin:3px 0 0">Dùng khi công việc đã thực hiện nhưng không có kế hoạch dự kiến.</p>{% endif %}
                </div>
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
                  <textarea id="history-content" name="content" placeholder="Nhập nội dung bảo dưỡng hoặc sửa chữa" required>{{ selected_history.get("Nội dung", "") if selected_history else "" }}</textarea>
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
          <script>
            (() => {
              const exitButton = document.querySelector("[data-maintenance-exit]");
              if (!exitButton) return;
              const forms = Array.from(document.querySelectorAll(".maintenance-card form"));
              const trackedFields = (form) => Array.from(form.querySelectorAll("input:not([type='hidden']):not([type='submit']):not([type='button']), textarea, select"));
              const snapshots = forms.map((form) => {
                const fields = trackedFields(form);
                return { fields, values: fields.map((field) => field.type === "checkbox" ? field.checked : field.value) };
              });
              const isDirty = () => snapshots.some(({ fields, values }) => fields.some((field, index) => {
                const value = field.type === "checkbox" ? field.checked : field.value;
                return value !== values[index];
              }));

              exitButton.addEventListener("click", (event) => {
                if (isDirty() && !window.confirm("Bạn đang nhập dở. Bạn có muốn thoát mà không lưu không?")) {
                  event.preventDefault();
                }
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
        parking_location=parking_location,
        parking_location_options=parking_location_options,
        area_admin=area_admin,
        next_item=next_item,
        latest_service_date=latest_service_date,
        maintenance_state=maintenance_state,
        plan_actionable=plan_actionable,
        plan_type=plan_type,
        vehicle_rows=vehicle_rows,
        vehicle_page_rows=vehicle_page_rows,
        vehicle_page=vehicle_page,
        vehicle_total_pages=vehicle_total_pages,
        vehicle_pagination_pages=vehicle_pagination_pages,
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
        tomorrow=(now_vn().date() + timedelta(days=1)).isoformat(),
        date_input_value=date_input_value,
        format_short_date=format_short_date,
        format_money_input=format_money_input,
        now_vn=now_vn,
        normalize_manufacture_year=normalize_manufacture_year,
        normalize_history_cost_type=normalize_history_cost_type,
        normalize_plan_type=normalize_plan_type,
        root_admin=is_admin(),
        center_admin=is_center_admin(),
        center_filter=center_filter,
        center_filter_label=CENTER_FILTER_LABELS.get(center_filter, CENTER_FILTER_LABELS[CENTER_FILTER_ALL]),
        center_filters=CENTER_FILTER_LABELS,
    )
    return page(content, title="Quản lý dữ liệu xe", active="data")


@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin():
    if not can_manage_data():
        return Response("Forbidden", status=403)

    requested_center = request.form.get("center") if request.method == "POST" else request.args.get("center")
    center_filter = normalize_center_filter(requested_center)
    session["admin_center_filter"] = center_filter
    admin_view = request.args.get("view", "access")
    if admin_view not in ("access", "audit", "accounts"):
        admin_view = "access"
    if admin_view in ("audit", "accounts") and not is_admin():
        return Response("Forbidden", status=403)
    if admin_view == "accounts" and is_admin():
        center_filter = CENTER_FILTER_ALL
        session["admin_center_filter"] = center_filter
    data = load_data(["xe", "access"])
    data["xe"] = center_filtered_vehicle_df(scoped_vehicle_df(data["xe"]), center_filter)
    scope_plates = scoped_vehicle_plates(data, include_inactive=True)
    data["xe"] = scoped_vehicle_df(data["xe"])
    active_vehicles = active_vehicle_df(data["xe"])
    plates = safe_unique(active_vehicles, "Biển số")
    plate_labels = plate_label_map(active_vehicles)

    if request.method == "POST":
        action = request.form.get("action")
        if action in ("rotate_area_admin", "set_area_admin_key"):
            if not is_admin():
                return Response("Forbidden", status=403)
            area_code = request.form.get("area_code", "").strip()
            custom_key = request.form.get("custom_key", "") if action == "set_area_admin_key" else ""
            new_key, error = set_area_admin_key(area_code, custom_key)
            if new_key:
                flash(
                    f"Đã đổi mã quản trị {CENTER_FILTER_LABELS.get(area_code, area_code)}. Mã mới: {new_key}",
                    "success",
                )
            else:
                flash(error or "Không thể đổi mã tài khoản khu vực.", "error")
            return redirect(url_for("admin", view="accounts"))
        if action == "toggle_area_admin":
            if not is_admin():
                return Response("Forbidden", status=403)
            area_code = request.form.get("area_code", "").strip()
            active = request.form.get("active") == "1"
            if set_area_admin_active(area_code, active):
                flash(
                    f"Đã {'bật' if active else 'tắt'} tài khoản {CENTER_FILTER_LABELS.get(area_code, area_code)}.",
                    "success",
                )
            else:
                flash("Không tìm thấy tài khoản khu vực.", "error")
            return redirect(url_for("admin", view="accounts"))
        if action == "create":
            selected_plates = parse_plate_list(request.form.get("plates", ""))
            ttl_hours = parse_ttl_hours(request.form.get("ttl_hours", ACCESS_TTL_HOURS))
            invalid_plates = [plate for plate in selected_plates if plate not in plates]
            if not selected_plates:
                flash("Vui lòng chọn ít nhất một xe.", "error")
            elif invalid_plates:
                flash("Có biển số không hợp lệ.", "error")
            else:
                code, created_at = create_access_code(
                    selected_plates,
                    ttl_hours,
                    current_area_code() or (center_filter if center_filter != CENTER_FILTER_ALL else MAIN_CENTER_CODE),
                )
                flash(f"Đã tạo mã {code} cho {len(selected_plates)} xe, hiệu lực {ttl_hours} giờ.", "success")
        elif action == "revoke":
            code = request.form.get("code", "").strip()
            if code and access_code_is_in_scope(code, scope_plates) and revoke_access_code(code):
                flash(f"Đã thu hồi mã {code}.", "success")
            else:
                flash("Mã không tồn tại hoặc nằm ngoài phạm vi quản lý.", "error")
        elif action == "extend":
            code = request.form.get("code", "").strip()
            extra_hours = parse_ttl_hours(request.form.get("extra_hours", ACCESS_TTL_HOURS))
            if code and access_code_is_in_scope(code, scope_plates) and extend_access_code(code, extra_hours):
                flash(f"Đã gia hạn mã {code} thêm {extra_hours} giờ.", "success")
            else:
                flash("Mã không tồn tại hoặc nằm ngoài phạm vi quản lý.", "error")
        return redirect(url_for("admin", view=admin_view, center=center_filter))

    access_rows = []
    if not data["access"].empty:
        for _, row in data["access"].iterrows():
            code = str(row.get("MaTruyCap", ""))
            if ADMIN_KEY and hmac.compare_digest(code, ADMIN_KEY):
                continue
            code_plates = parse_plate_list(row.get("BienSo", ""))
            if center_filter != CENTER_FILTER_ALL and (
                not code_plates or not set(code_plates).issubset(scope_plates)
            ):
                continue
            access_rows.append(
                {
                    "code": code,
                    "plate": format_plate_list(row.get("BienSo", ""), plate_labels),
                    "created": format_datetime_display(row.get("ThoiDiemCap", "")),
                    "ttl": f'{parse_ttl_hours(row.get("ThoiHanGio", ACCESS_TTL_HOURS))} giờ',
                    "remaining": remaining_text(row.get("ThoiDiemCap", ""), row.get("ThoiHanGio", ACCESS_TTL_HOURS)),
                    "active": access_is_active(row.get("ThoiDiemCap", ""), row.get("ThoiHanGio", ACCESS_TTL_HOURS)),
                    "issuer": admin_actor_label(row.get("NguoiCap", "")) if row.get("NguoiCap") else "Trước đây",
                    "issued_area": CENTER_FILTER_LABELS.get(str(row.get("DonViCap", "")), ""),
                    "sort_time": parse_cap_time_safe(row.get("ThoiDiemCap", "")),
                }
            )
    access_rows = sorted(access_rows, key=lambda item: item["sort_time"], reverse=True)

    audit_rows = []
    area_accounts = area_admin_accounts() if is_admin() else []
    audit_query = request.args.get("audit_q", "").strip()
    audit_actor = request.args.get("audit_actor", "").strip()
    audit_action = request.args.get("audit_action", "").strip()
    audit_from = request.args.get("audit_from", "").strip()
    audit_to = request.args.get("audit_to", "").strip()
    audit_action_options = [
        {"value": action, "label": label}
        for action, label in sorted(AUDIT_ACTION_LABELS.items(), key=lambda item: item[1])
    ]
    audit_actor_options = [
        {"code": code, "label": admin_actor_label(code)}
        for code in admin_actor_codes()
    ]
    if admin_view == "audit":
        audit_clauses = []
        audit_params = []
        actor_codes = admin_actor_codes()
        if actor_codes:
            placeholders = ", ".join("?" for _ in actor_codes)
            audit_clauses.append(f"actor_code IN ({placeholders})")
            audit_params.extend(actor_codes)
        else:
            audit_clauses.append("1 = 0")
        if center_filter != CENTER_FILTER_ALL:
            if scope_plates:
                placeholders = ", ".join("?" for _ in scope_plates)
                audit_clauses.append(f"plate IN ({placeholders})")
                audit_params.extend(sorted(scope_plates))
            else:
                audit_clauses.append("1 = 0")
        if audit_query:
            like_query = f"%{audit_query.lower()}%"
            audit_clauses.append(
                "(LOWER(COALESCE(actor_code, '')) LIKE ? "
                "OR LOWER(COALESCE(action, '')) LIKE ? "
                "OR LOWER(COALESCE(plate, '')) LIKE ? "
                "OR LOWER(COALESCE(detail, '')) LIKE ?)"
            )
            audit_params.extend([like_query] * 4)
        if audit_actor in actor_codes:
            audit_clauses.append("actor_code = ?")
            audit_params.append(audit_actor)
        if audit_action in AUDIT_ACTION_LABELS:
            audit_clauses.append("action = ?")
            audit_params.append(audit_action)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", audit_from):
            audit_clauses.append("substr(created_at, 1, 10) >= ?")
            audit_params.append(audit_from)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", audit_to):
            audit_clauses.append("substr(created_at, 1, 10) <= ?")
            audit_params.append(audit_to)
        init_db()
        with db_connect() as conn:
            rows = conn.execute(
                f"""
                SELECT created_at, actor_code, action, plate, detail, ip_addr
                FROM audit_logs
                WHERE {' AND '.join(audit_clauses)}
                ORDER BY id DESC
                LIMIT 200
                """,
                audit_params,
            ).fetchall()
        for row in rows:
            audit_rows.append(
                {
                    "created_at": format_datetime_display(row["created_at"]),
                    "actor": admin_actor_label(row["actor_code"]),
                    "action": AUDIT_ACTION_LABELS.get(row["action"], row["action"] or ""),
                    "plate": row["plate"] or "",
                    "detail": row["detail"] or "",
                    "ip_addr": row["ip_addr"] or "",
                }
            )

    content = render_template_string(
        r"""
        <section class="hero">
          <div>
            <h1>{{ 'Lịch sử truy cập' if admin_view == 'audit' else ('Quản lý tài khoản khu vực' if admin_view == 'accounts' else 'Quản lý mã truy cập') }}</h1>
          </div>
          {% if root_admin %}
            <div class="toolbar center-filter-toolbar">
              {% for center_code, center_label in center_filters.items() %}
                <a class="btn {{ 'primary' if center_filter == center_code else '' }}" href="{{ url_for('admin', view=admin_view, center=center_code) }}">{{ center_label }}</a>
              {% endfor %}
            </div>
          {% else %}
            <span class="badge">{{ center_filter_label }}</span>
          {% endif %}
        </section>

        <div class="admin-shell">
          <aside class="panel admin-sidebar">
            <div>
              <h2>Quản trị</h2>
            </div>
            <nav class="admin-menu">
              <a href="{{ url_for('admin_data', center=center_filter) }}">
                Quản lý xe
              </a>
              <a class="{{ 'active' if admin_view == 'access' else '' }}" href="{{ url_for('admin', view='access', center=center_filter) }}">
                Quản lý mã truy cập
              </a>
              {% if root_admin %}
                <a class="{{ 'active' if admin_view == 'accounts' else '' }}" href="{{ url_for('admin', view='accounts') }}">
                  Tài khoản khu vực
                </a>
                <a class="{{ 'active' if admin_view == 'audit' else '' }}" href="{{ url_for('admin', view='audit', center=center_filter) }}">
                  Lịch sử thao tác
                </a>
              {% endif %}
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
                  <input type="hidden" name="center" value="{{ center_filter }}">
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

          {% elif admin_view == 'accounts' %}
          <style>
            .area-admin-key-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:flex-end; }
            .area-admin-key-form { display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap; }
            .area-admin-key-form input { min-width:230px; }
            .area-admin-key-help { width:100%; }
          </style>
          <section class="panel admin-management-panel">
            <div class="section-head">
              <div>
                <h2>Tài khoản quản trị khu vực</h2>
                <p class="subtle">Admin tổng có thể đổi mã, bật/tắt tài khoản và xem đơn vị cấp mã người dùng.</p>
              </div>
              <span class="badge">{{ area_accounts|length }} khu vực</span>
            </div>
            <div class="access-code-list">
              {% for account in area_accounts %}
                <article class="access-code-card {{ 'active' if account.active else 'expired' }}">
                  <div class="access-code-main">
                    <span class="access-code-label">Đơn vị</span>
                    <strong class="access-code-value">{{ account.label }}</strong>
                    <span class="status-pill {{ 'active' if account.active else 'expired' }}">{{ 'Đang hoạt động' if account.active else 'Đang tắt' }}</span>
                  </div>
                  <div class="access-code-meta">
                    <span class="access-code-label">Mã hiện tại</span>
                    <strong class="access-code-value code">{{ account.key_hint or 'Chưa cấp mã' }}</strong>
                    <span class="subtle">Cập nhật {{ format_datetime_display(account.updated_at) }}</span>
                  </div>
                  <div class="access-card-actions area-admin-key-actions">
                    <form method="post" class="area-admin-key-form">
                      <input type="hidden" name="action" value="set_area_admin_key">
                      <input type="hidden" name="area_code" value="{{ account.area_code }}">
                      <input name="custom_key" type="text" minlength="6" maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._-]{5,63}" placeholder="Tự đặt mã hoặc để trống" aria-label="Mã tự đặt cho {{ account.label }}">
                      <button class="btn primary" type="submit" onclick="return confirm('Đổi mã đăng nhập tài khoản này? Mã cũ sẽ mất hiệu lực.')">Đặt mã</button>
                      <span class="subtle area-admin-key-help">Bỏ trống để hệ thống tạo mã ngẫu nhiên.</span>
                    </form>
                    <form method="post">
                      <input type="hidden" name="action" value="set_area_admin_key">
                      <input type="hidden" name="area_code" value="{{ account.area_code }}">
                      <input type="hidden" name="custom_key" value="">
                      <button class="btn" type="submit" onclick="return confirm('Tạo mã ngẫu nhiên mới? Mã cũ sẽ mất hiệu lực.')">Tạo ngẫu nhiên</button>
                    </form>
                    <form method="post">
                      <input type="hidden" name="action" value="toggle_area_admin">
                      <input type="hidden" name="area_code" value="{{ account.area_code }}">
                      <input type="hidden" name="active" value="{{ '0' if account.active else '1' }}">
                      <button class="btn {{ 'danger' if account.active else 'primary' }}" type="submit">{{ 'Tắt tài khoản' if account.active else 'Bật tài khoản' }}</button>
                    </form>
                  </div>
                </article>
              {% endfor %}
            </div>
          </section>
          <section class="panel admin-management-panel">
            <div class="section-head">
              <div>
                <h2>Mã truy cập do các khu vực cấp</h2>
                <p class="subtle">Danh sách bên dưới hiển thị người cấp và đơn vị cấp; admin tổng có thể gia hạn hoặc thu hồi.</p>
              </div>
              <span class="badge">{{ access_rows|length }} mã</span>
            </div>
            {% if access_rows %}
              <div class="access-code-list">
                {% for row in access_rows %}
                  <article class="access-code-card {{ 'active' if row.active else 'expired' }}">
                    <div class="access-code-main">
                      <span class="access-code-label">Mã truy cập</span>
                      <strong class="access-code-value code">{{ row.code }}</strong>
                      <span class="status-pill {{ 'active' if row.active else 'expired' }}">{{ 'Còn hạn' if row.active else 'Hết hạn' }}</span>
                    </div>
                    <div class="access-code-meta">
                      <span class="access-code-label">Đơn vị cấp</span>
                      <strong class="access-code-value">{{ row.issued_area or 'Hội sở' }}</strong>
                      <span class="subtle">{{ row.issuer }}</span>
                    </div>
                    <div class="access-code-status">
                      <span class="access-code-label">Xe được cấp quyền</span>
                      <strong class="access-code-value access-code-plate">{{ row.plate }}</strong>
                      <span class="subtle">Cấp lúc {{ row.created }} · {{ row.ttl }}</span>
                    </div>
                    <div class="access-card-actions">
                      <form method="post">
                        <input type="hidden" name="action" value="extend">
                        <input type="hidden" name="code" value="{{ row.code }}">
                        <input name="extra_hours" type="number" min="1" max="720" value="24" aria-label="Số giờ gia hạn">
                        <button class="btn primary" type="submit">Gia hạn</button>
                      </form>
                      <form method="post">
                        <input type="hidden" name="action" value="revoke">
                        <input type="hidden" name="code" value="{{ row.code }}">
                        <button class="btn danger" type="submit">Thu hồi</button>
                      </form>
                    </div>
                  </article>
                {% endfor %}
              </div>
            {% else %}
              <div class="empty">Chưa có mã truy cập.</div>
            {% endif %}
          </section>
          {% else %}
          <section class="panel admin-management-panel">
            <div class="section-head">
              <h2>Lịch sử thao tác quản trị</h2>
              <span class="badge">{{ audit_rows|length }} mục gần nhất</span>
            </div>
            <form class="audit-filter" method="get">
              <input type="hidden" name="view" value="audit">
              <input type="hidden" name="center" value="{{ center_filter }}">
              <div class="audit-filter-grid">
                <div class="field"><label for="audit-q">Tìm kiếm</label><input id="audit-q" name="audit_q" value="{{ audit_query }}" placeholder="Biển số, nội dung hoặc thao tác"></div>
                <div class="field"><label for="audit-actor">Người thực hiện</label><select id="audit-actor" name="audit_actor"><option value="">Tất cả quản trị</option>{% for option in audit_actor_options %}<option value="{{ option.code }}" {{ 'selected' if audit_actor == option.code else '' }}>{{ option.label }}</option>{% endfor %}</select></div>
                <div class="field"><label for="audit-action">Loại thao tác</label><select id="audit-action" name="audit_action"><option value="">Tất cả thao tác</option>{% for option in audit_action_options %}<option value="{{ option.value }}" {{ 'selected' if audit_action == option.value else '' }}>{{ option.label }}</option>{% endfor %}</select></div>
                <div class="field"><label for="audit-from">Từ ngày</label><input id="audit-from" type="date" name="audit_from" value="{{ audit_from }}"></div>
                <div class="field"><label for="audit-to">Đến ngày</label><input id="audit-to" type="date" name="audit_to" value="{{ audit_to }}"></div>
              </div>
              <div class="form-actions"><button class="btn primary" type="submit">Tra cứu</button><a class="btn" href="{{ url_for('admin', view='audit', center=center_filter) }}">Xóa bộ lọc</a></div>
            </form>
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
        area_accounts=area_accounts,
        audit_rows=audit_rows,
        audit_query=audit_query,
        audit_actor=audit_actor,
        audit_action=audit_action,
        audit_from=audit_from,
        audit_to=audit_to,
        audit_actor_options=audit_actor_options,
        audit_action_options=audit_action_options,
        admin_view=admin_view,
        root_admin=is_admin(),
        center_admin=is_center_admin(),
        center_filter=center_filter,
        center_filter_label=CENTER_FILTER_LABELS.get(center_filter, CENTER_FILTER_LABELS[CENTER_FILTER_ALL]),
        center_filters=CENTER_FILTER_LABELS,
        format_datetime_display=format_datetime_display,
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
