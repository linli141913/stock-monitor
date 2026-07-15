#!/usr/bin/env python3
import argparse
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import acceptance_replay  # noqa: E402
import alert_repository  # noqa: E402
import database  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(
        description="在临时数据库中回放固定提醒样本并生成验收报告",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=BACKEND / "tests" / "fixtures" / "golden_alert_cases.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "reports" / "acceptance_report.md",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    original_db_path = database.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DB_PATH = Path(temp_dir) / "acceptance.db"
            database.init_db()
            alert_repository.init_alert_tables()
            report = acceptance_replay.replay_cases(
                acceptance_replay.load_cases(args.fixture),
                save_event=alert_repository.save_alert_event,
            )
    finally:
        database.DB_PATH = original_db_path

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        acceptance_replay.render_markdown(report),
        encoding="utf-8",
    )
    print(f"验收报告：{args.report}")
    print(report["historicalMetrics"]["reason"])
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
