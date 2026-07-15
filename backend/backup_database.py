"""为运行中的 SQLite 数据库创建一致性备份并生成 SHA-256 校验文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Union


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent / "data" / "stock_monitor.db"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_database_backup(
    source_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
    target_dir: Optional[Union[str, Path]] = None,
    *,
    now: Optional[datetime] = None,
) -> dict:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在：{source}")

    destination_dir = (
        Path(target_dir).expanduser().resolve()
        if target_dir is not None
        else source.parent / "backups"
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup_path = destination_dir / f"{source.stem}-{timestamp}.db.backup"
    if backup_path.exists():
        raise FileExistsError(f"备份文件已存在：{backup_path}")

    with sqlite3.connect(source) as source_connection:
        with sqlite3.connect(backup_path) as backup_connection:
            source_connection.backup(backup_connection)

    with sqlite3.connect(backup_path) as verification_connection:
        quick_check = verification_connection.execute("PRAGMA quick_check").fetchone()
    if not quick_check or quick_check[0] != "ok":
        raise RuntimeError("备份完整性校验失败")

    digest = _sha256(backup_path)
    checksum_path = backup_path.with_name(f"{backup_path.name}.sha256")
    checksum_path.write_text(
        f"{digest}  {backup_path.name}\n",
        encoding="utf-8",
    )
    return {
        "backupPath": str(backup_path),
        "checksumPath": str(checksum_path),
        "sha256": digest,
        "sizeBytes": backup_path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="安全备份股票监测 SQLite 运行数据")
    parser.add_argument("--source", default=str(DEFAULT_DATABASE_PATH))
    parser.add_argument("--target-dir", default=None)
    args = parser.parse_args()
    result = create_database_backup(args.source, args.target_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
