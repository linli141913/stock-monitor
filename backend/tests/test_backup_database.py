import hashlib
import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class DatabaseBackupTests(unittest.TestCase):
    def test_live_sqlite_backup_is_readable_and_has_matching_sha256(self):
        module_path = ROOT / "backup_database.py"
        self.assertTrue(module_path.is_file(), "尚未提供可校验的数据库备份工具")

        spec = importlib.util.spec_from_file_location("backup_database", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "runtime.db"
            backup_dir = Path(temp_dir) / "backups"
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE samples (value TEXT)")
                connection.execute("INSERT INTO samples VALUES ('真实运行数据')")
                connection.commit()

            result = module.create_database_backup(
                source,
                backup_dir,
                now=datetime(2026, 7, 15, 18, 30, 45),
            )

            backup_path = Path(result["backupPath"])
            checksum_path = Path(result["checksumPath"])
            self.assertTrue(backup_path.is_file())
            self.assertTrue(checksum_path.is_file())
            with sqlite3.connect(backup_path) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM samples").fetchone()[0],
                    "真实运行数据",
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")

            digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
            self.assertEqual(result["sha256"], digest)
            self.assertTrue(checksum_path.read_text(encoding="utf-8").startswith(digest))

    def test_backup_opens_source_with_sqlite_read_only_mode(self):
        module_path = ROOT / "backup_database.py"
        spec = importlib.util.spec_from_file_location("backup_database", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "runtime.db"
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE samples (value TEXT)")

            real_connect = sqlite3.connect
            with patch.object(
                module.sqlite3,
                "connect",
                wraps=real_connect,
            ) as mocked_connect:
                module.create_database_backup(
                    source,
                    Path(temp_dir) / "backups",
                    now=datetime(2026, 7, 18, 14, 0),
                )

            source_call = mocked_connect.call_args_list[0]
            self.assertEqual(
                source_call.args[0],
                f"{source.resolve().as_uri()}?mode=ro",
            )
            self.assertTrue(source_call.kwargs["uri"])


if __name__ == "__main__":
    unittest.main()
