"""受控写入少量阶段6风险公告正文快照。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

import database
from radar.leader_risk_content_delivery import (
    LeaderRiskContentSelection,
    deliver_leader_risk_document_contents,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository


def _selection(value: str) -> LeaderRiskContentSelection:
    document_id, separator, category = value.partition("=")
    if not separator or not document_id.strip() or not category.strip():
        raise argparse.ArgumentTypeError(
            "公告选择格式必须是 documentId=candidateCategory"
        )
    return LeaderRiskContentSelection(
        document_id=document_id.strip(),
        candidate_category=category.strip(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="下载并原子保存不超过3份真实巨潮公告正文快照"
    )
    parser.add_argument("--database", default=database.DB_PATH)
    parser.add_argument("--review-batch-id", required=True)
    parser.add_argument(
        "--document",
        action="append",
        required=True,
        type=_selection,
    )
    parser.add_argument(
        "--confirm-production-write",
        action="store_true",
    )
    args = parser.parse_args()

    database_path = Path(args.database).expanduser().resolve(strict=True)
    if not database_path.is_file():
        parser.error("数据库路径不是现有文件")
    connection = sqlite3.connect(database_path, timeout=20)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 20000")
        report = deliver_leader_risk_document_contents(
            LeaderRiskReviewRepository(connection),
            args.review_batch_id,
            args.document,
            confirmed=args.confirm_production_write,
        )
    finally:
        connection.close()

    payload = asdict(report)
    payload["status"] = report.status.value
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.status.value == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
