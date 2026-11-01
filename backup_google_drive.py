"""Create a consistent application snapshot and upload it with rclone.

The script intentionally uses only the Python standard library so it can run
from the existing application virtual environment. Configure the rclone
remote once, then run this script from Task Scheduler.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
BACKUP_DIR = PROJECT_DIR / "backup"
LOG_PATH = BACKUP_DIR / "backup.log"
RCLONE_EXE = Path(os.getenv("TRACUUXE_RCLONE_EXE", r"C:\Tools\rclone\rclone.exe"))
RCLONE_CONFIG = Path(
    os.getenv("TRACUUXE_RCLONE_CONFIG", str(BACKUP_DIR / "rclone.conf"))
)
REMOTE_PATH = os.getenv(
    "TRACUUXE_BACKUP_REMOTE", "gdrive:VIBE_CODE_AUTOBACKUP"
).rstrip("/")
KEEP_LATEST = int(os.getenv("TRACUUXE_BACKUP_KEEP_LATEST", "7"))

# Runtime and backup material must not be copied into a new snapshot.
SKIP_DIRS = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
    "backup",
    "logs",
    "node_modules",
    "venv",
    ".venv",
}
SKIP_FILE_NAMES = {
    "tracuuxe.sqlite3",
    "tracuuxe.sqlite3-wal",
    "tracuuxe.sqlite3-shm",
}
SKIP_SUFFIXES = {".pyc", ".log"}
VIETNAM_TZ = timezone(timedelta(hours=7))


def configure_logging() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )


def read_env_value(name: str) -> str | None:
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        return value
    return None


def database_path() -> Path:
    configured = read_env_value("TRACUUXE_DB_PATH")
    if not configured:
        return PROJECT_DIR / "tracuuxe.sqlite3"
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_DIR / path


def copy_sqlite_snapshot(source_path: Path, destination_path: Path) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"Không tìm thấy database: {source_path}")
    source = sqlite3.connect(str(source_path), timeout=30)
    destination = sqlite3.connect(str(destination_path))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()


def should_skip(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.relative_to(PROJECT_DIR).parts[:-1]):
        return True
    if path.name in SKIP_FILE_NAMES:
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    return False


def add_source_files(archive: zipfile.ZipFile, archive_path: Path) -> int:
    added = 0
    for path in PROJECT_DIR.rglob("*"):
        if not path.is_file() or path == archive_path or should_skip(path):
            continue
        archive.write(path, path.relative_to(PROJECT_DIR).as_posix())
        added += 1
    return added


def create_archive() -> tuple[Path, Path]:
    now = datetime.now(VIETNAM_TZ)
    archive_path = BACKUP_DIR / f"TraCuuXe-{now:%Y%m%d-%H%M%S}.zip"
    staging_dir = Path(tempfile.mkdtemp(prefix="snapshot-", dir=BACKUP_DIR))
    db_snapshot = staging_dir / "tracuuxe.sqlite3"
    copy_sqlite_snapshot(database_path(), db_snapshot)

    try:
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            file_count = add_source_files(archive, archive_path)
            archive.write(db_snapshot, "data/tracuuxe.sqlite3")
        logging.info("Đã tạo snapshot %s (%s file source)", archive_path.name, file_count)
        return archive_path, staging_dir
    except Exception:
        archive_path.unlink(missing_ok=True)
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def run_rclone(*args: str) -> subprocess.CompletedProcess[str]:
    command = [
        str(RCLONE_EXE),
        "--config",
        str(RCLONE_CONFIG),
        *args,
    ]
    logging.info("Chạy rclone %s", args[0] if args else "")
    return subprocess.run(command, check=True, capture_output=True, text=True)


def upload_and_prune(archive_path: Path) -> None:
    remote_file = f"{REMOTE_PATH}/{archive_path.name}"
    run_rclone(
        "copyto",
        str(archive_path),
        remote_file,
        "--retries",
        "3",
        "--low-level-retries",
        "10",
        "--log-level",
        "NOTICE",
        "--log-file",
        str(LOG_PATH),
    )
    listing = run_rclone(
        "lsf",
        REMOTE_PATH,
        "--files-only",
        "--include",
        "TraCuuXe-*.zip",
        "--log-level",
        "NOTICE",
        "--log-file",
        str(LOG_PATH),
    )
    backup_names = [
        line.strip()
        for line in listing.stdout.splitlines()
        if re.fullmatch(r"TraCuuXe-\d{8}-\d{6}\.zip", line.strip())
    ]
    # The filename uses YYYYMMDD-HHMMSS, so reverse lexical order is newest first.
    backup_names.sort(reverse=True)
    for old_name in backup_names[KEEP_LATEST:]:
        run_rclone(
            "deletefile",
            f"{REMOTE_PATH}/{old_name}",
            "--log-level",
            "NOTICE",
            "--log-file",
            str(LOG_PATH),
        )
        logging.info("Đã xóa bản backup cũ %s", old_name)
    logging.info(
        "Đã tải %s lên %s và giữ lại %s bản mới nhất",
        archive_path.name,
        REMOTE_PATH,
        KEEP_LATEST,
    )


def main() -> int:
    configure_logging()
    archive_path: Path | None = None
    staging_dir: Path | None = None
    try:
        if not RCLONE_EXE.exists():
            raise FileNotFoundError(f"Không tìm thấy rclone: {RCLONE_EXE}")
        if not RCLONE_CONFIG.exists():
            raise FileNotFoundError(
                f"Chưa cấu hình Google Drive: {RCLONE_CONFIG}"
            )
        archive_path, staging_dir = create_archive()
        upload_and_prune(archive_path)
        logging.info("Backup hoàn tất")
        return 0
    except Exception:
        logging.exception("Backup thất bại")
        return 1
    finally:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
