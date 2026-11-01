"""Import the area-based Hanoi cash-management vehicle list into TraCuuXe."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime, timedelta

import pandas as pd


AREA_CODES = {
    "Cửa Nam": "cua_nam",
    "BPKD Bắc Hà Nội": "bac_ha_noi",
    "BPKD Tây Hà Nội": "tay_ha_noi",
    "BPKD Vĩnh Phúc": "vinh_phuc",
    "TT QLTM Hải Phòng": "hai_phong",
    "TT QLTM Bắc Ninh": "bac_ninh",
}
ACTIVE_STATUS = "Đang hoạt động"


def normalize_plate(value):
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", "", str(value).strip().upper())


def now_vn():
    return datetime.utcnow() + timedelta(hours=7)


def load_rows(workbook):
    # The source files may include a title/header row, or may be headerless.
    # Reading without a header and keeping rows whose first column is numeric
    # supports both formats without dropping the first vehicle.
    frame = pd.read_excel(workbook, sheet_name=0, header=None, dtype=object)
    if frame.shape[1] < 5:
        raise ValueError("Workbook must contain at least five columns")

    valid = frame[pd.to_numeric(frame.iloc[:, 0], errors="coerce").notna()]
    rows = []
    for _, row in valid.iterrows():
        plate = normalize_plate(row.iloc[2])
        if not plate or plate == "NAN":
            continue
        year = row.iloc[3]
        if pd.isna(year):
            year = ""
        elif isinstance(year, float) and year.is_integer():
            year = int(year)
        location = "" if pd.isna(row.iloc[4]) else str(row.iloc[4]).strip()
        area_code = AREA_CODES.get(location)
        if not area_code:
            raise ValueError(f"Unknown parking area for {plate}: {location or '<empty>'}")
        car_type = "" if pd.isna(row.iloc[1]) else str(row.iloc[1]).strip()
        rows.append((plate, car_type, str(year).strip(), area_code, location))
    return rows


def ensure_schema(conn):
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
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cars)")}
    if "center" not in columns:
        conn.execute("ALTER TABLE cars ADD COLUMN center TEXT DEFAULT 'main'")
    if "parking_location" not in columns:
        conn.execute("ALTER TABLE cars ADD COLUMN parking_location TEXT")
    conn.execute("UPDATE cars SET center = 'main' WHERE center IS NULL OR TRIM(center) = ''")


def import_rows(workbook, database, dry_run=False):
    rows = load_rows(workbook)
    print(f"Found {len(rows)} valid vehicles")
    if dry_run:
        for row in rows:
            print(" | ".join(row))
        return

    os.makedirs(os.path.dirname(os.path.abspath(database)), exist_ok=True)
    with sqlite3.connect(database) as conn:
        ensure_schema(conn)
        stamp = now_vn().strftime("%Y-%m-%d %H:%M")
        conn.executemany(
            """
            INSERT INTO cars
                (plate, car_type, manufacture_year, status, center, parking_location, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plate) DO UPDATE SET
                car_type = excluded.car_type,
                manufacture_year = excluded.manufacture_year,
                status = excluded.status,
                center = excluded.center,
                parking_location = excluded.parking_location,
                updated_at = excluded.updated_at
            """,
            [
                (plate, car_type, year, ACTIVE_STATUS, area_code, location, stamp)
                for plate, car_type, year, area_code, location in rows
            ],
        )
        conn.commit()
    print(f"Imported {len(rows)} vehicles into {database}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook")
    parser.add_argument(
        "--database",
        default=os.path.join(os.path.dirname(__file__), "tracuuxe.sqlite3"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    import_rows(args.workbook, args.database, args.dry_run)


if __name__ == "__main__":
    main()
